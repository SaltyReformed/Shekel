#!/bin/bash
# Shekel Budget App -- Database Restore
#
# Restores the Shekel database from a backup file produced by backup.sh.
#
# Restore sequence:
#     1. Stop the application container (if running)
#     2. Terminate existing database connections
#     3. Drop and recreate the database with schemas
#     4. Restore from the backup file (via psql --single-transaction)
#     5. Restart the application container (entrypoint runs Alembic migrations)
#     6. Verify the restore with basic sanity checks
#
# Usage:
#     ./scripts/restore.sh <backup_file>
#     ./scripts/restore.sh --skip-confirm <backup_file>
#
# Options:
#     --skip-confirm  Skip the interactive confirmation prompt
#     --help          Show this help message
#
# Exit codes:
#     0   Restore completed successfully
#     1   Fatal error (missing file, decryption failure, restore failure)

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-shekel-prod-db}"
APP_CONTAINER="${APP_CONTAINER:-shekel-prod-app}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# ── Functions ────────────────────────────────────────────────────

log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] <backup_file>

Restore the Shekel database from a backup file.

This will:
    1. Stop the application container (if running)
    2. Drop and recreate the database
    3. Restore from the backup file
    4. Run pending Alembic migrations (via app container restart)
    5. Verify the restore

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --skip-confirm  Skip the interactive confirmation prompt
    --help          Show this help message

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL
    APP_CONTAINER                 Docker container name for the app
    PGUSER                        PostgreSQL user
    PGDATABASE                    PostgreSQL database name
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase (required for .gpg files)
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
        [yY][eE][sS]|[yY])
            log "INFO" "Restore confirmed by user"
            ;;
        *)
            log "INFO" "Restore cancelled by user"
            exit 0
            ;;
    esac
}

stop_app() {
    # Stop the app container if it exists and is running.
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

    # Terminate existing connections to the target database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PGDATABASE}' AND pid <> pg_backend_pid();" \
        >/dev/null 2>&1 || true

    # Drop and recreate the database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${PGDATABASE};"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "CREATE DATABASE ${PGDATABASE} OWNER ${PGUSER};"

    # Recreate schemas. The pg_dump --clean output includes DROP/CREATE for
    # tables within schemas, but the schemas themselves must exist first
    # for the restore to succeed.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" --quiet -c \
        "CREATE SCHEMA IF NOT EXISTS ref;
         CREATE SCHEMA IF NOT EXISTS auth;
         CREATE SCHEMA IF NOT EXISTS budget;
         CREATE SCHEMA IF NOT EXISTS salary;
         CREATE SCHEMA IF NOT EXISTS system;"

    log "INFO" "Database recreated with schemas"
}

restore_backup() {
    local backup_file="$1"

    log "INFO" "Restoring from: ${backup_file}"

    # Determine if the file is encrypted.
    if [[ "${backup_file}" == *.gpg ]]; then
        if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
            log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
            exit 1
        fi
        log "INFO" "Decrypting backup..."
        # Decrypt → decompress → pipe to psql inside the db container.
        echo "${BACKUP_ENCRYPTION_PASSPHRASE}" | gpg --batch --passphrase-fd 0 --quiet -d "${backup_file}" \
            | gunzip \
            | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" \
                --quiet --single-transaction --set ON_ERROR_STOP=1 --output /dev/null
    else
        # Decompress → pipe to psql inside the db container.
        gunzip -c "${backup_file}" \
            | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" \
                --quiet --single-transaction --set ON_ERROR_STOP=1 --output /dev/null
    fi

    log "INFO" "Database restore complete"
}

start_app() {
    # Restart the app container. The entrypoint runs init_database.py which
    # detects the existing database and applies any pending Alembic migrations.
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

        # If no health check, just check if it's running.
        local running
        running=$(docker inspect --format='{{.State.Running}}' "${APP_CONTAINER}" 2>/dev/null || echo "false")
        if [[ "${running}" == "true" && "${health}" == "none" ]]; then
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
    # Quick sanity checks against the restored database.
    log "INFO" "Running post-restore verification..."

    local user_count
    user_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM auth.users;" 2>/dev/null | tr -d ' ')

    if [[ -z "${user_count}" || "${user_count}" -eq 0 ]]; then
        log "WARNING" "No users found in the restored database. Verify the backup file."
    else
        log "INFO" "Verification: ${user_count} user(s) found in restored database"
    fi

    local period_count
    period_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM budget.pay_periods;" 2>/dev/null | tr -d ' ')
    log "INFO" "Verification: ${period_count} pay period(s) in restored database"

    local table_count
    table_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema IN ('ref','auth','budget','salary','system');" 2>/dev/null | tr -d ' ')
    log "INFO" "Verification: ${table_count} table(s) across all schemas"
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local skip_confirm=false
    local backup_file=""

    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-confirm)
                skip_confirm=true
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

    BACKUP_FILE="${backup_file}"

    check_prerequisites

    # Validate encryption requirements before any destructive operations.
    if [[ "${backup_file}" == *.gpg && -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
        log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
        exit 1
    fi

    # Confirmation prompt (default: No).
    if [[ "${skip_confirm}" == false ]]; then
        confirm_restore
    fi

    stop_app
    drop_and_recreate_database
    restore_backup "${backup_file}"
    start_app
    verify_restore

    log "INFO" "Restore complete."
    if app_container_exists; then
        log "INFO" "Review the application at http://localhost:5000 to verify."
    else
        log "INFO" "Run 'flask run' and verify the application manually."
    fi
}

main "$@"
