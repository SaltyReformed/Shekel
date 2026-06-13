#!/bin/bash
# Shekel Budget App -- Backup Verification
#
# Verifies a backup by restoring it to a temporary database, running
# sanity check queries and the Python integrity check script, then
# dropping the temporary database.
#
# The production database is never touched.
#
# Usage:
#     ./scripts/verify_backup.sh <backup_file>
#
# Options:
#     --help          Show this help message
#
# Exit codes:
#     0   All checks passed
#     1   One or more checks failed
#     2   Warnings only (no critical failures)
#
# Cron example (weekly, Sunday at 3:00 AM):
#     0 3 * * 0 /path/to/shekel/scripts/verify_backup.sh \
#         $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) \
#         >> /var/log/shekel_backup.log 2>&1

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-shekel-prod-db}"
APP_CONTAINER="${APP_CONTAINER:-shekel-prod-app}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
# Per-run suffix so two overlapping verification runs cannot drop each
# other's temp database mid-restore (OPS/SH-27).
VERIFY_DB="${VERIFY_DB:-shekel_verify_$$}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# ── Functions ────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") <backup_file>

Verify a Shekel backup by restoring to a temporary database and running
sanity checks.

This will:
    1. Create a temporary database (${VERIFY_DB})
    2. Restore the backup into it
    3. Run sanity check queries (row counts, user check, date ranges)
    4. Run the integrity check script
    5. Drop the temporary database

The production database is never touched.

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --help          Show this help message

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL
    APP_CONTAINER                 Docker container name for the app
    PGUSER                        PostgreSQL user
    PGDATABASE                    Production database name (for reference)
    VERIFY_DB                     Temporary database name for verification
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase (required for .gpg files)
EOF
}

# shellcheck disable=SC2329 # invoked indirectly as the EXIT trap handler registered by 'trap cleanup EXIT' in main, never called by name
cleanup() {
    # Trap handler: always drop the temporary database on exit.
    log "INFO" "Cleaning up: dropping temporary database ${VERIFY_DB}"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${VERIFY_DB};" 2>/dev/null || true
}

get_db_password() {
    # Resolve POSTGRES_PASSWORD from the running DB container.
    #
    # Refuses to fall back to the historical public default
    # ``shekel_pass`` -- audit finding F-109 / Commit C-38 follow-up
    # Issue 3.  An empty or unreadable POSTGRES_PASSWORD is a
    # misconfiguration that must surface as a hard failure here
    # rather than silently constructing a leaked-credential URL for
    # the integrity-check subprocess.
    #
    # check_prerequisites() has already verified that the DB
    # container is running, so the only realistic failure modes for
    # this function are: (a) the container was started without
    # POSTGRES_PASSWORD in its env, (b) the env channel was scrubbed
    # mid-run, or (c) the container died between the prerequisite
    # check and this call.  All three are operator-actionable and
    # are reported with the same diagnostic.
    #
    # Logs go to stderr so the function's stdout (which the caller
    # captures via command substitution) carries the password and
    # only the password.
    local password
    password=$(docker exec "${DB_CONTAINER}" printenv POSTGRES_PASSWORD 2>/dev/null) \
        || password=""
    if [[ -z "${password}" ]]; then
        log "ERROR" "POSTGRES_PASSWORD is not discoverable from container '${DB_CONTAINER}'." >&2
        log "ERROR" "Ensure the container is running and the env channel populates POSTGRES_PASSWORD." >&2
        log "ERROR" "Refusing to fall back to the historical public default (audit F-109 / Commit C-38)." >&2
        return 1
    fi
    printf '%s' "${password}"
}

check_prerequisites() {
    # shellcheck disable=SC2310 # require_db_container is a boolean predicate; the trailing '|| exit 1' propagates its failure explicitly, so masked errexit inside it is irrelevant
    require_db_container "${DB_CONTAINER}" || exit 1
}

create_temp_database() {
    log "INFO" "Creating temporary database: ${VERIFY_DB}"

    # Drop if it exists from a previous failed run.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${VERIFY_DB};" 2>/dev/null || true

    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "CREATE DATABASE ${VERIFY_DB} OWNER ${PGUSER};"

    # Create schemas (shared SHEKEL_APP_SCHEMAS list -- OPS/SH-06).
    # shellcheck disable=SC2312 # shekel_create_schema_sql only printf's over a static array and cannot fail; its return value carries no signal worth propagating
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" --quiet -c \
        "$(shekel_create_schema_sql)"

    log "INFO" "Temporary database created"
}

restore_to_temp() {
    local backup_file="$1"
    log "INFO" "Restoring backup to temporary database..."

    # shellcheck disable=SC2310 # require_passphrase_for is a boolean predicate; the trailing '|| return 1' propagates its failure explicitly, so masked errexit inside it is irrelevant
    require_passphrase_for "${backup_file}" "${BACKUP_ENCRYPTION_PASSPHRASE}" || return 1
    backup_stream "${backup_file}" \
        | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" \
            --quiet --single-transaction --set ON_ERROR_STOP=1 --output /dev/null

    log "INFO" "Restore to temporary database complete"
}

run_sanity_checks() {
    # Sanity queries against the temporary database.
    local failures=0

    log "INFO" "Running sanity checks..."

    # Check 1: auth.users has at least one row.
    local user_count
    user_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM auth.users;" | tr -d ' ')
    if [[ "${user_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] auth.users: ${user_count} user(s)"
    else
        log "ERROR" "  [FAIL] auth.users: 0 users"
        failures=$((failures + 1))
    fi

    # Check 2: budget.pay_periods row count and date range.
    local period_info
    period_info=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*), MIN(start_date), MAX(end_date) FROM budget.pay_periods;" | tr -d ' ')
    local period_count min_date max_date
    period_count=$(echo "${period_info}" | cut -d'|' -f1)
    min_date=$(echo "${period_info}" | cut -d'|' -f2)
    max_date=$(echo "${period_info}" | cut -d'|' -f3)
    if [[ "${period_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] budget.pay_periods: ${period_count} periods (${min_date} to ${max_date})"
    else
        log "INFO" "  [INFO] budget.pay_periods: 0 periods (may be expected for a fresh database)"
    fi

    # Check 3: budget.transactions row count (informational).
    local txn_count
    txn_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM budget.transactions;" | tr -d ' ')
    log "INFO" "  [INFO] budget.transactions: ${txn_count} row(s)"

    # Check 4: budget.accounts has rows.
    local acct_count
    acct_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM budget.accounts;" | tr -d ' ')
    if [[ "${acct_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] budget.accounts: ${acct_count} account(s)"
    else
        log "ERROR" "  [FAIL] budget.accounts: 0 accounts"
        failures=$((failures + 1))
    fi

    # Check 5: ref tables are populated.
    local ref_count
    ref_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM ref.account_types;" | tr -d ' ')
    if [[ "${ref_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] ref.account_types: ${ref_count} type(s)"
    else
        log "ERROR" "  [FAIL] ref.account_types: 0 types (reference data missing)"
        failures=$((failures + 1))
    fi

    # Check 6: system.audit_log table exists (may have 0 rows, that's OK).
    local audit_exists
    audit_exists=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'system' AND table_name = 'audit_log');" | tr -d ' ')
    if [[ "${audit_exists}" == "t" ]]; then
        local audit_count
        audit_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
            "SELECT COUNT(*) FROM system.audit_log;" | tr -d ' ')
        log "INFO" "  [PASS] system.audit_log: table exists (${audit_count} row(s))"
    else
        log "WARNING" "  [WARN] system.audit_log: table does not exist"
    fi

    # Check 7: alembic_version exists and has a value.
    local alembic_version
    alembic_version=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT version_num FROM public.alembic_version LIMIT 1;" 2>/dev/null | tr -d ' ') || true
    if [[ -n "${alembic_version}" ]]; then
        log "INFO" "  [PASS] alembic_version: ${alembic_version}"
    else
        log "WARNING" "  [WARN] alembic_version: not found (may indicate pre-migration backup)"
    fi

    return ${failures}
}

run_integrity_checks() {
    # Run the Python integrity check script against the temporary database.
    log "INFO" "Running integrity checks against temporary database..."

    local exit_code=0

    # Resolve POSTGRES_PASSWORD once, up front, with EXPLICIT failure
    # propagation: this function is invoked as `run_integrity_checks ||
    # integrity_code=$?`, and bash disables errexit inside a function
    # called in a || context -- so the previous comment's claim that
    # "set -e propagates" was false and the F-109 hard-fail never fired;
    # the script proceeded with an empty password (OPS/SH-07, verified
    # during the audit). `|| return 1` does not depend on errexit.
    local db_password
    # shellcheck disable=SC2310 # get_db_password's own '|| return 1' already makes its failure explicit; the trailing '|| return 1' here propagates it, so masked errexit inside the function is irrelevant
    db_password=$(get_db_password) || return 1

    # Determine how to run the integrity check script.
    # If the app container exists, run inside it. Otherwise, run locally.
    if docker inspect "${APP_CONTAINER}" &>/dev/null; then
        # Production: run inside the app container with overridden
        # DATABASE_URL, delivered via --env-file on a 0600 temp file so the
        # credential never enters the docker CLI argv, where it would be
        # world-readable via /proc/<pid>/cmdline for the duration of the
        # integrity run (OPS/SH-08 -- the same channel the F-022 scrub and
        # the docker-secrets migration exist to close).
        local env_file
        env_file=$(umask 077 && mktemp)
        printf 'DATABASE_URL=postgresql://%s:%s@db:5432/%s\n' \
            "${PGUSER}" "${db_password}" "${VERIFY_DB}" >"${env_file}"
        docker exec --env-file "${env_file}" \
            "${APP_CONTAINER}" python scripts/integrity_check.py --verbose || exit_code=$?
        rm -f "${env_file}"
    else
        # Development: run locally with the verify database URL.
        # The DB is exposed on localhost via docker-compose.dev.yml port mapping.
        local verify_url="postgresql://${PGUSER}:${db_password}@localhost:5432/${VERIFY_DB}"
        DATABASE_URL="${verify_url}" python scripts/integrity_check.py --verbose || exit_code=$?
    fi

    if [[ ${exit_code} -eq 0 ]]; then
        log "INFO" "Integrity checks: ALL PASSED"
    elif [[ ${exit_code} -eq 2 ]]; then
        log "WARNING" "Integrity checks: WARNINGS detected (no critical failures)"
    else
        log "ERROR" "Integrity checks: CRITICAL FAILURES detected"
    fi

    return ${exit_code}
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local backup_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
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

    # Validate encryption requirements before starting.
    if [[ "${backup_file}" == *.gpg && -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
        log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
        exit 1
    fi

    check_prerequisites

    # Register cleanup trap -- always drop the temp database on exit.
    trap cleanup EXIT

    create_temp_database
    restore_to_temp "${backup_file}"

    local sanity_failures=0
    local integrity_code=0

    # shellcheck disable=SC2310 # run_sanity_checks deliberately returns its failure COUNT; '|| sanity_failures=$?' captures that count for the summary and exit-code decision below, which is the function's contract, not a swallowed fatal error
    run_sanity_checks || sanity_failures=$?
    # shellcheck disable=SC2310 # run_integrity_checks deliberately returns integrity_check.py's 0/1/2 code; '|| integrity_code=$?' captures it for the summary and is mapped to the script's exit code below, which is the function's contract, not a swallowed fatal error
    run_integrity_checks || integrity_code=$?

    # Report final status.
    echo ""
    log "INFO" "============================================================"
    log "INFO" "  BACKUP VERIFICATION SUMMARY"
    log "INFO" "============================================================"
    log "INFO" "  Backup file:      ${backup_file}"
    log "INFO" "  Sanity checks:    ${sanity_failures} failure(s)"
    log "INFO" "  Integrity checks: exit code ${integrity_code}"

    if [[ ${sanity_failures} -eq 0 && ${integrity_code} -eq 0 ]]; then
        log "INFO" "  Status: PASS"
        log "INFO" "============================================================"
        # cleanup runs via trap
        exit 0
    elif [[ ${integrity_code} -eq 2 && ${sanity_failures} -eq 0 ]]; then
        # -eq, not -le: integrity_check.py's contract is 0=pass, 1=CRITICAL
        # failures, 2=warnings only. The previous -le 2 mapped CRITICAL
        # failures to PASS WITH WARNINGS / exit 2, so cron monitoring keyed
        # on exit 1 never fired for a critically-broken backup (OPS/SH-02).
        log "WARNING" "  Status: PASS WITH WARNINGS"
        log "INFO" "============================================================"
        exit 2
    else
        log "ERROR" "  Status: FAIL"
        log "INFO" "============================================================"
        exit 1
    fi
}

main "$@"
