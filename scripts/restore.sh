#!/bin/bash
# Shekel Budget App -- Database Restore
#
# Restores the Shekel database from a backup file produced by backup.sh.
#
# Restore sequence:
#     1. VALIDATE the backup artifact end-to-end (decrypt + gzip integrity +
#        non-empty) -- nothing destructive happens until the artifact proves
#        restorable (polyglot audit 2026-06-12, OPS/SH-01: the previous
#        sequence dropped the production database before reading a single
#        byte, so a wrong passphrase or truncated file left an empty DB with
#        no recovery path)
#     2. Take a pre-restore SAFETY DUMP of the current database, encrypted
#        with the backup key file (Josh's ruling R-BAL306).  Without a usable
#        key file the script stops BEFORE step 1: for an encrypted backup
#        always, for an unencrypted one unless --plaintext-safety-dump
#        chooses an unencrypted copy or --skip-safety-dump skips it
#     3. Stop the application container (if running)
#     4. Terminate existing database connections
#     5. Drop and recreate the database with schemas
#     6. Restore from the backup file (via psql --single-transaction)
#     7. Restart the application container (entrypoint runs Alembic migrations)
#     8. Verify the restore with basic sanity checks
#
# If anything fails between the drop and a completed restore, the script
# prints recovery guidance naming the safety dump.
#
# Usage:
#     ./scripts/restore.sh <backup_file>
#     ./scripts/restore.sh --skip-confirm <backup_file>
#
# Options:
#     --skip-confirm      Skip the interactive confirmation prompt
#     --skip-safety-dump  Proceed without the pre-restore safety dump (for the
#                         disaster case where the current database is already
#                         lost/unreadable and the dump step itself fails)
#     --plaintext-safety-dump
#                         Write the safety dump UNENCRYPTED (.sql.gz)
#     --help              Show this help message
#
# An encrypted backup (.gpg) and the encrypted safety dump both use the
# backup key file (SHEKEL_BACKUP_KEY_FILE in _backup_lib.sh; --help prints
# its path).
#
# Exit codes:
#     0   Restore completed successfully
#     1   Fatal error (missing file, validation failure, restore failure)

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-$(shekel_prod_db_container)}"
APP_CONTAINER="${APP_CONTAINER:-$(shekel_prod_app_container)}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"

# Set once the DROP DATABASE has run; drives the recovery-guidance trap.
DESTRUCTIVE_WINDOW=false
SAFETY_DUMP_PATH=""
# The safety dump's partial file while it is written (take_safety_dump).
SAFETY_DUMP_PARTIAL=""

# ── Functions ────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] <backup_file>

Restore the Shekel database from a backup file.

This will:
    1. Validate the backup artifact (decrypt + integrity) BEFORE any
       destructive step
    2. Take a pre-restore safety dump of the current database, encrypted
       with the key file ${SHEKEL_BACKUP_KEY_FILE}
    3. Stop the application container (if running)
    4. Drop and recreate the database
    5. Restore from the backup file
    6. Run pending Alembic migrations (via app container restart)
    7. Verify the restore

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --skip-confirm      Skip the interactive confirmation prompt
    --skip-safety-dump  Proceed without the pre-restore safety dump
    --plaintext-safety-dump
                        Write the safety dump UNENCRYPTED (.sql.gz)
    --help              Show this help message

An encrypted backup (.gpg) and the encrypted safety dump need the key file
${SHEKEL_BACKUP_KEY_FILE} (owned by the user running this script, no access
for the group or others).  Without it the script stops before touching
anything, except for an unencrypted backup under --plaintext-safety-dump or
--skip-safety-dump.

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL
    APP_CONTAINER                 Docker container name for the app
    PGUSER                        PostgreSQL user
    PGDATABASE                    PostgreSQL database name
    BACKUP_LOCAL_DIR              Where the safety dump is written
EOF
}

recovery_guidance() {
    # ERR/EXIT-path guidance once the database has been dropped: the operator
    # must know the state and the way back (OPS/SH-01: the previous script
    # died under set -e leaving an empty database and no instructions).
    if [[ "${DESTRUCTIVE_WINDOW}" == true ]]; then
        {
            echo ""
            echo "============================================================"
            echo "  RESTORE FAILED INSIDE THE DESTRUCTIVE WINDOW"
            echo "============================================================"
            echo "  The database '${PGDATABASE}' was dropped and may be empty"
            echo "  or partially restored."
            if [[ -n "${SAFETY_DUMP_PATH}" ]]; then
                echo "  A pre-restore safety dump of the PREVIOUS state exists:"
                echo "      ${SAFETY_DUMP_PATH}"
                echo "  Recover it with:"
                echo "      ./scripts/restore.sh --skip-safety-dump ${SAFETY_DUMP_PATH}"
            else
                echo "  No safety dump was taken (--skip-safety-dump was set)."
                echo "  Recover from the most recent nightly backup."
            fi
            echo "  The app container was left STOPPED on purpose."
            echo "============================================================"
        } >&2
    fi
}
trap recovery_guidance ERR

check_prerequisites() {
    # shellcheck disable=SC2310 # require_db_container is a boolean predicate (explicit if-not/return 1, no internal set -e reliance); the trailing || exit 1 is the intended abort on failure
    require_db_container "${DB_CONTAINER}" || exit 1
}

app_container_exists() {
    # Check if the app container exists (may or may not be running).
    docker inspect "${APP_CONTAINER}" &>/dev/null
}

confirm_restore() {
    # Interactive confirmation with default No.
    echo ""
    echo "============================================================"
    echo "  WARNING: This will REPLACE ALL DATA in the Shekel database"
    echo "============================================================"
    echo ""
    echo "  Backup file:  ${BACKUP_FILE}"
    echo "  Database:     ${PGDATABASE}"
    echo "  DB container: ${DB_CONTAINER}"
    echo ""
    read -r -p "  Are you sure you want to continue? [y/N] " response
    case "${response}" in
        [yY][eE][sS] | [yY])
            log "INFO" "Restore confirmed by user"
            ;;
        *)
            log "INFO" "Restore cancelled by user"
            exit 0
            ;;
    esac
}

take_safety_dump() {
    # Dump the CURRENT database before destroying it, through backup.sh's own
    # writer: encrypted with the key file unless $1 is "plaintext" (main has
    # checked the key), never on disk unencrypted when encrypted, 0600 in a
    # directory it creates 0700.  The writer removes its partial file when
    # the pipeline fails; the EXIT trap set here removes it when an
    # interrupt (Ctrl-C, SIGTERM) ends the run mid-dump, and finds nothing
    # once the rename has run.
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local dump_path="${BACKUP_LOCAL_DIR}/pre_restore_safety_${ts}.sql.gz"
    if [[ "$1" != plaintext ]]; then
        dump_path="${dump_path}.gpg"
    fi

    log "INFO" "Taking pre-restore safety dump: ${dump_path}"
    partial_path_for SAFETY_DUMP_PARTIAL "${dump_path}"
    trap 'rm -f -- "${SAFETY_DUMP_PARTIAL}"' EXIT
    write_dump_artifact "${dump_path}" "Pre-restore safety dump FAILED; nothing was dropped. If the current database is already lost (the disaster-recovery case), re-run with --skip-safety-dump."
    SAFETY_DUMP_PATH="${dump_path}"
    log "INFO" "Safety dump complete: ${dump_path}"
}

stop_app() {
    # Stop the app container if it exists and is running.
    # shellcheck disable=SC2310 # app_container_exists is a boolean predicate (docker inspect &>/dev/null); the container-absent non-zero status IS its intended return, not an error to abort on
    if app_container_exists; then
        log "INFO" "Stopping application container: ${APP_CONTAINER}"
        docker stop "${APP_CONTAINER}" 2>/dev/null || true
        log "INFO" "Application container stopped"
    else
        log "INFO" "Application container '${APP_CONTAINER}' not found (dev mode); skipping stop"
    fi
}

drop_and_recreate_database() {
    log "INFO" "Dropping and recreating database: ${PGDATABASE}"
    DESTRUCTIVE_WINDOW=true

    # Terminate existing connections to the target database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PGDATABASE}' AND pid <> pg_backend_pid();" \
        >/dev/null 2>&1 || true

    # Drop and recreate the database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${PGDATABASE};"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "CREATE DATABASE ${PGDATABASE} OWNER ${PGUSER};"

    # Recreate schemas (shared SHEKEL_APP_SCHEMAS list -- OPS/SH-06). The
    # pg_dump --clean output includes DROP/CREATE for tables within schemas,
    # but the schemas themselves must exist first for the restore to succeed.
    # shellcheck disable=SC2312 # shekel_create_schema_sql only printfs static CREATE SCHEMA statements, so it cannot meaningfully fail; there is no inner return value worth surfacing here
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" --quiet -c \
        "$(shekel_create_schema_sql)"

    log "INFO" "Database recreated with schemas"
}

restore_backup() {
    local backup_file="$1"

    log "INFO" "Restoring from: ${backup_file}"
    # backup_stream handles .gpg decryption (gpg reads the key file itself)
    # and gzip decompression for both artifact shapes (OPS/SH-06: the
    # pipeline previously existed twice here and once in verify_backup.sh).
    backup_stream "${backup_file}" \
        | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" \
            --quiet --single-transaction --set ON_ERROR_STOP=1 --output /dev/null

    log "INFO" "Database restore complete"
    DESTRUCTIVE_WINDOW=false
}

start_app() {
    # Restart the app container. The entrypoint runs init_database.py which
    # detects the existing database and applies any pending Alembic migrations.
    # shellcheck disable=SC2310 # app_container_exists is a boolean predicate (docker inspect &>/dev/null); the container-absent non-zero status IS its intended return, not an error to abort on
    if ! app_container_exists; then
        log "INFO" "Application container '${APP_CONTAINER}' not found (dev mode)"
        log "INFO" "Run 'flask db upgrade' manually to apply any pending migrations"
        return 0
    fi

    log "INFO" "Starting application container: ${APP_CONTAINER}"
    docker start "${APP_CONTAINER}"

    # Wait for the container to be ready.
    local retries=30
    while [[ ${retries} -gt 0 ]]; do
        # Check for a health check first.
        local health
        health=$(docker inspect --format='{{.State.Health.Status}}' "${APP_CONTAINER}" 2>/dev/null || echo "none")
        if [[ "${health}" == "healthy" ]]; then
            log "INFO" "Application container is healthy"
            return 0
        fi

        # If no health check, just check if it's running: the shell scripts'
        # one running-check (plan step balance:X-dm, ruling R-BAL254), which
        # _backup_lib.sh loads.
        # shellcheck disable=SC2310 # container_running is a boolean predicate (one captured docker inspect); its 0/1 is exactly what this if tests.
        if [[ "${health}" == "none" ]] && container_running "${APP_CONTAINER}"; then
            # Give the entrypoint a moment to finish initialization.
            sleep 3
            log "INFO" "Application container is running"
            return 0
        fi

        # Container might still be starting.
        sleep 2
        retries=$((retries - 1))
    done

    log "WARNING" "Application container did not become ready within 60s"
    log "WARNING" "Check logs: docker logs ${APP_CONTAINER}"
}

verify_restore() {
    # Quick sanity checks against the restored database. Each query's failure
    # is surfaced, not masked (OPS/SH-17: 2>/dev/null previously turned a
    # psql failure into a blank interpolated into a 'Verification:' line).
    log "INFO" "Running post-restore verification..."

    local user_count
    if ! user_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM auth.users;" | tr -d ' '); then
        log "ERROR" "Verification query failed: auth.users count"
        return 1
    fi
    if [[ -z "${user_count}" || "${user_count}" -eq 0 ]]; then
        log "WARNING" "No users found in the restored database. Verify the backup file."
    else
        log "INFO" "Verification: ${user_count} user(s) found in restored database"
    fi

    local period_count
    if ! period_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM budget.pay_periods;" | tr -d ' '); then
        log "ERROR" "Verification query failed: budget.pay_periods count"
        return 1
    fi
    log "INFO" "Verification: ${period_count} pay period(s) in restored database"

    local table_count
    if ! table_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema IN ('ref','auth','budget','salary','system');" | tr -d ' '); then
        log "ERROR" "Verification query failed: table count"
        return 1
    fi
    log "INFO" "Verification: ${table_count} table(s) across all schemas"
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local skip_confirm=false
    local skip_safety_dump=false
    local plaintext_safety_dump=false
    local backup_file=""

    refuse_retired_passphrase_variable

    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-confirm)
                skip_confirm=true
                shift
                ;;
            --skip-safety-dump)
                skip_safety_dump=true
                shift
                ;;
            --plaintext-safety-dump)
                plaintext_safety_dump=true
                shift
                ;;
            --help)
                usage
                exit 0
                ;;
            -*)
                log "ERROR" "Unknown option: $1"
                usage
                exit 1
                ;;
            *)
                backup_file="$1"
                shift
                ;;
        esac
    done

    if [[ -z "${backup_file}" ]]; then
        log "ERROR" "No backup file specified"
        usage
        exit 1
    fi

    if [[ ! -f "${backup_file}" ]]; then
        log "ERROR" "Backup file not found: ${backup_file}"
        exit 1
    fi

    # How the safety dump is taken, and whether the key is needed, decided
    # before anything is read or touched (R-BAL306).
    local safety_dump=encrypted
    if [[ "${skip_safety_dump}" == true && "${plaintext_safety_dump}" == true ]]; then
        log "ERROR" "--skip-safety-dump and --plaintext-safety-dump contradict each other; pass one."
        exit 1
    elif [[ "${skip_safety_dump}" == true ]]; then
        safety_dump=skip
    elif [[ "${plaintext_safety_dump}" == true ]]; then
        safety_dump=plaintext
    fi
    if [[ "${backup_file}" == *.gpg ]]; then
        require_backup_key "The backup ${backup_file} is encrypted with it."
    elif [[ "${safety_dump}" == encrypted ]]; then
        require_backup_key "The pre-restore safety dump is encrypted with it.  Or pass --plaintext-safety-dump for an UNENCRYPTED safety dump, or --skip-safety-dump for none."
    fi
    if [[ "${safety_dump}" == plaintext ]]; then
        note_plaintext_choice "--plaintext-safety-dump" "the pre-restore safety dump"
    fi

    BACKUP_FILE="${backup_file}"

    check_prerequisites

    # The OPS/SH-01 gate: prove the artifact decrypts and decompresses
    # end-to-end BEFORE anything destructive runs.
    # shellcheck disable=SC2310 # validate_backup_artifact returns explicit codes (subshell-local set -o pipefail so pipe failures reach the caller without errexit); the trailing || exit 1 IS the OPS/SH-01 abort-before-destructive gate
    validate_backup_artifact "${backup_file}" || exit 1

    # Confirmation prompt (default: No).
    if [[ "${skip_confirm}" == false ]]; then
        confirm_restore
    fi

    if [[ "${safety_dump}" == skip ]]; then
        log "WARNING" "Pre-restore safety dump SKIPPED (--skip-safety-dump)"
    else
        take_safety_dump "${safety_dump}"
    fi

    stop_app
    drop_and_recreate_database
    restore_backup "${backup_file}"
    start_app
    verify_restore

    log "INFO" "Restore complete."
    if [[ -n "${SAFETY_DUMP_PATH}" ]]; then
        log "INFO" "Pre-restore safety dump retained at: ${SAFETY_DUMP_PATH}"
        log "INFO" "(delete it once the restored state is confirmed good)"
    fi
    # shellcheck disable=SC2310 # app_container_exists is a boolean predicate (docker inspect &>/dev/null); the container-absent non-zero status IS its intended return, not an error to abort on
    if app_container_exists; then
        log "INFO" "Review the application at http://localhost:5000 to verify."
    else
        log "INFO" "Run 'flask run' and verify the application manually."
    fi
}

main "$@"
