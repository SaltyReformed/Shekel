#!/bin/bash
# Shekel Budget App -- Backup Retention Pruning
#
# Prunes old backup files according to a tiered retention policy:
#     Daily backups:   kept for 7 days    (configurable)
#     Weekly (Sunday): kept for 4 weeks   (configurable)
#     Monthly (1st):   kept for 6 months  (configurable)
#
# Retention classification is based on the date in the backup filename
# (shekel_backup_YYYYMMDD_HHMMSS.sql.gz), NOT the file modification time.
#
# Applies independently to both local and NAS directories.
#
# Usage:
#     ./scripts/backup_retention.sh [OPTIONS]
#
# Options:
#     --local-dir DIR     Local backup directory (default: /var/backups/shekel)
#     --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
#     --dry-run           Print what would be deleted without deleting
#     --help              Show this help message
#
# Exit codes:
#     0   Retention cleanup completed (missing directories produce warnings, not errors)
#     1   Fatal error (bad arguments)
#
# Cron example (daily at 2:30 AM, after backup at 2:00 AM):
#     30 2 * * * /path/to/shekel/scripts/backup_retention.sh >> /var/log/shekel_backup.log 2>&1

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"
BACKUP_NAS_DIR="${BACKUP_NAS_DIR:-/mnt/nas/backups/shekel}"

# Retention periods.
RETENTION_DAILY_DAYS="${RETENTION_DAILY_DAYS:-7}"
RETENTION_WEEKLY_WEEKS="${RETENTION_WEEKLY_WEEKS:-4}"
RETENTION_MONTHLY_MONTHS="${RETENTION_MONTHLY_MONTHS:-6}"

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

Prune old Shekel backup files according to tiered retention policy.

Retention tiers:
    Daily backups:   kept for ${RETENTION_DAILY_DAYS} days
    Weekly (Sunday): kept for ${RETENTION_WEEKLY_WEEKS} weeks
    Monthly (1st):   kept for ${RETENTION_MONTHLY_MONTHS} months

Options:
    --local-dir DIR     Local backup directory (default: /var/backups/shekel)
    --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
    --dry-run           Print what would be deleted without deleting
    --help              Show this help message

Environment Variables:
    BACKUP_LOCAL_DIR          Local backup directory
    BACKUP_NAS_DIR            NAS backup directory
    RETENTION_DAILY_DAYS      Days to keep daily backups (default: 7)
    RETENTION_WEEKLY_WEEKS    Weeks to keep weekly/Sunday backups (default: 4)
    RETENTION_MONTHLY_MONTHS  Months to keep monthly/1st backups (default: 6)
EOF
}

extract_date_from_filename() {
    # Extract YYYYMMDD from shekel_backup_YYYYMMDD_HHMMSS.sql.gz[.gpg]
    local filename="$1"
    echo "${filename}" | grep -oP 'shekel_backup_\K\d{8}'
}

is_sunday_backup() {
    # Check if the backup date (YYYYMMDD) falls on a Sunday.
    # GNU date: %u returns 1=Monday .. 7=Sunday.
    local date_str="$1"  # YYYYMMDD
    local formatted="${date_str:0:4}-${date_str:4:2}-${date_str:6:2}"
    local dow
    dow=$(date -d "${formatted}" +%u 2>/dev/null) || return 1
    [[ "${dow}" -eq 7 ]]
}

is_first_of_month_backup() {
    # Check if the backup date (YYYYMMDD) is the 1st of the month.
    local date_str="$1"  # YYYYMMDD
    [[ "${date_str:6:2}" == "01" ]]
}

days_old() {
    # Calculate how many days old a backup is based on the date in its filename.
    local date_str="$1"  # YYYYMMDD
    local formatted="${date_str:0:4}-${date_str:4:2}-${date_str:6:2}"
    local backup_epoch today_epoch
    backup_epoch=$(date -d "${formatted}" +%s 2>/dev/null) || return 1
    today_epoch=$(date +%s)
    echo $(( (today_epoch - backup_epoch) / 86400 ))
}

prune_directory() {
    # Apply retention policy to a single directory.
    local dir="$1"
    local dry_run="$2"
    local pruned=0

    if [[ ! -d "${dir}" ]]; then
        log "WARNING" "Directory does not exist, skipping: ${dir}"
        return 0
    fi

    log "INFO" "Processing directory: ${dir}"

    # Calculate cutoff thresholds in days.
    local weekly_cutoff_days=$(( RETENTION_WEEKLY_WEEKS * 7 ))
    local monthly_cutoff_days=$(( RETENTION_MONTHLY_MONTHS * 30 ))

    # Iterate over backup files in the directory.
    # The glob matches both .sql.gz and .sql.gz.gpg files.
    for filepath in "${dir}"/shekel_backup_*.sql.gz*; do
        # Skip if glob matched nothing (no files).
        [[ -f "${filepath}" ]] || continue

        local filename
        filename=$(basename "${filepath}")
        local date_str
        date_str=$(extract_date_from_filename "${filename}") || continue
        local age
        age=$(days_old "${date_str}") || continue

        local keep=false

        # Tier evaluation order: monthly > weekly > daily.
        # A file that qualifies for a higher tier is always kept.

        # Monthly tier: 1st of month, kept for RETENTION_MONTHLY_MONTHS months.
        if is_first_of_month_backup "${date_str}" && [[ ${age} -le ${monthly_cutoff_days} ]]; then
            keep=true
        fi

        # Weekly tier: Sunday backups, kept for RETENTION_WEEKLY_WEEKS weeks.
        if [[ "${keep}" == false ]] && is_sunday_backup "${date_str}" && [[ ${age} -le ${weekly_cutoff_days} ]]; then
            keep=true
        fi

        # Daily tier: all backups within RETENTION_DAILY_DAYS.
        if [[ "${keep}" == false ]] && [[ ${age} -le ${RETENTION_DAILY_DAYS} ]]; then
            keep=true
        fi

        if [[ "${keep}" == false ]]; then
            if [[ "${dry_run}" == true ]]; then
                log "INFO" "[DRY RUN] Would delete: ${filename} (age: ${age}d)"
            else
                rm -f "${filepath}"
                log "INFO" "Deleted: ${filename} (age: ${age}d)"
            fi
            pruned=$((pruned + 1))
        fi
    done

    if [[ ${pruned} -eq 0 ]]; then
        log "INFO" "No files to prune in ${dir}"
    else
        local verb="Pruned"
        [[ "${dry_run}" == true ]] && verb="Would prune"
        log "INFO" "${verb} ${pruned} file(s) from ${dir}"
    fi
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local dry_run=false

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
            --dry-run)
                dry_run=true
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

    log "INFO" "Retention policy: daily=${RETENTION_DAILY_DAYS}d, weekly=${RETENTION_WEEKLY_WEEKS}w, monthly=${RETENTION_MONTHLY_MONTHS}m"

    # Process both directories independently.
    prune_directory "${BACKUP_LOCAL_DIR}" "${dry_run}"
    prune_directory "${BACKUP_NAS_DIR}" "${dry_run}"

    log "INFO" "Retention cleanup complete"
}

main "$@"
