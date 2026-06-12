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
# Safety rails (polyglot audit 2026-06-12, OPS/SH-05 + OPS/SH-16):
#   * Deletion requires --force; without it every run is a dry-run. The
#     house shell standard is confirm-or---force for destructive
#     operations, and this script deletes the disaster-recovery story.
#   * A keep-floor (RETENTION_MIN_KEEP, default 3) is enforced per
#     directory: pruning never reduces a directory below the N newest
#     backups, so a silently-dead backup producer can no longer let this
#     script age every remaining file out to zero.
#   * A staleness alarm: when the NEWEST backup in a directory is older
#     than RETENTION_STALE_ALERT_DAYS (default 2), the run logs ERROR and
#     exits 1 so cron/monitoring surfaces "backups stopped being made" --
#     the classic retention failure mode.
#
# Usage:
#     ./scripts/backup_retention.sh [OPTIONS]
#
# Options:
#     --local-dir DIR     Local backup directory (default: /var/backups/shekel)
#     --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
#     --force             Actually delete files (default: dry-run)
#     --dry-run           Explicit dry-run (the default; kept for cron clarity)
#     --help              Show this help message
#
# Exit codes:
#     0   Retention cleanup completed (missing directories produce warnings)
#     1   Fatal error (bad arguments) OR staleness alarm tripped
#
# Cron example (daily at 2:30 AM, after backup at 2:00 AM):
#     30 2 * * * /path/to/shekel/scripts/backup_retention.sh --force >> /var/log/shekel_backup.log 2>&1

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"
BACKUP_NAS_DIR="${BACKUP_NAS_DIR:-/mnt/nas/backups/shekel}"

# Retention periods.
RETENTION_DAILY_DAYS="${RETENTION_DAILY_DAYS:-7}"
RETENTION_WEEKLY_WEEKS="${RETENTION_WEEKLY_WEEKS:-4}"
RETENTION_MONTHLY_MONTHS="${RETENTION_MONTHLY_MONTHS:-6}"

# Safety rails.
RETENTION_MIN_KEEP="${RETENTION_MIN_KEEP:-3}"
RETENTION_STALE_ALERT_DAYS="${RETENTION_STALE_ALERT_DAYS:-2}"

# Set when any processed directory's newest backup exceeds the staleness
# threshold; drives the final exit code.
STALE_ALARM=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Prune old Shekel backup files according to tiered retention policy.

Retention tiers:
    Daily backups:   kept for ${RETENTION_DAILY_DAYS} days
    Weekly (Sunday): kept for ${RETENTION_WEEKLY_WEEKS} weeks
    Monthly (1st):   kept for ${RETENTION_MONTHLY_MONTHS} months

Safety rails:
    The ${RETENTION_MIN_KEEP} newest backups in a directory are never
    deleted, and the run fails (exit 1) when the newest backup is older
    than ${RETENTION_STALE_ALERT_DAYS} day(s) -- a stopped backup producer
    must surface here, not as an empty directory months later.

Options:
    --local-dir DIR     Local backup directory (default: /var/backups/shekel)
    --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
    --force             Actually delete files (default: dry-run)
    --dry-run           Explicit dry-run (the default)
    --help              Show this help message

Environment Variables:
    BACKUP_LOCAL_DIR            Local backup directory
    BACKUP_NAS_DIR              NAS backup directory
    RETENTION_DAILY_DAYS        Days to keep daily backups (default: 7)
    RETENTION_WEEKLY_WEEKS      Weeks to keep weekly/Sunday backups (default: 4)
    RETENTION_MONTHLY_MONTHS    Months to keep monthly/1st backups (default: 6)
    RETENTION_MIN_KEEP          Newest backups never deleted (default: 3)
    RETENTION_STALE_ALERT_DAYS  Newest-backup age that trips the alarm (default: 2)
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
    local force="$2"
    local pruned=0
    local total=0
    local newest_age=""

    if [[ ! -d "${dir}" ]]; then
        log "WARNING" "Directory does not exist, skipping: ${dir}"
        return 0
    fi

    log "INFO" "Processing directory: ${dir}"

    # Calculate cutoff thresholds in days.
    local weekly_cutoff_days=$(( RETENTION_WEEKLY_WEEKS * 7 ))
    local monthly_cutoff_days=$(( RETENTION_MONTHLY_MONTHS * 30 ))

    # Pass 1: classify. Files iterate in glob (lexicographic = chronological,
    # the timestamp is in the name) order, so delete_candidates is oldest-first
    # and the keep-floor rescue below can pop from the end (newest first).
    local delete_candidates=()
    local delete_ages=()
    for filepath in "${dir}"/shekel_backup_*.sql.gz*; do
        # Skip if glob matched nothing (no files).
        [[ -f "${filepath}" ]] || continue
        # Never count or touch in-flight temp files from backup.sh.
        [[ "${filepath}" == *.tmp ]] && continue
        total=$((total + 1))

        local filename
        filename=$(basename "${filepath}")
        local date_str
        date_str=$(extract_date_from_filename "${filename}") || continue
        local age
        age=$(days_old "${date_str}") || continue

        # Track the newest file's age for the staleness alarm.
        if [[ -z "${newest_age}" || ${age} -lt ${newest_age} ]]; then
            newest_age=${age}
        fi

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
            delete_candidates+=("${filepath}")
            delete_ages+=("${age}")
        fi
    done

    # Keep-floor (OPS/SH-05): never let pruning reduce the directory below
    # the RETENTION_MIN_KEEP newest backups. Rescue candidates newest-first
    # (from the end of the oldest-first candidate list).
    local would_remain=$(( total - ${#delete_candidates[@]} ))
    while [[ ${#delete_candidates[@]} -gt 0 && ${would_remain} -lt ${RETENTION_MIN_KEEP} ]]; do
        local rescued_idx=$(( ${#delete_candidates[@]} - 1 ))
        log "WARNING" "Keep-floor: retaining $(basename "${delete_candidates[${rescued_idx}]}") despite policy (directory would drop below ${RETENTION_MIN_KEEP} backups -- is the backup producer still running?)"
        unset 'delete_candidates[rescued_idx]' 'delete_ages[rescued_idx]'
        delete_candidates=("${delete_candidates[@]}")
        delete_ages=("${delete_ages[@]}")
        would_remain=$(( total - ${#delete_candidates[@]} ))
    done

    # Pass 2: delete (or report).
    local i
    for i in "${!delete_candidates[@]}"; do
        local filepath="${delete_candidates[${i}]}"
        local filename
        filename=$(basename "${filepath}")
        if [[ "${force}" == true ]]; then
            rm -f "${filepath}"
            log "INFO" "Deleted: ${filename} (age: ${delete_ages[${i}]}d)"
        else
            log "INFO" "[DRY RUN] Would delete: ${filename} (age: ${delete_ages[${i}]}d)"
        fi
        pruned=$((pruned + 1))
    done

    if [[ ${pruned} -eq 0 ]]; then
        log "INFO" "No files to prune in ${dir}"
    else
        local verb="Pruned"
        [[ "${force}" == false ]] && verb="Would prune"
        log "INFO" "${verb} ${pruned} file(s) from ${dir} (${would_remain} remain)"
    fi

    # Staleness alarm (OPS/SH-05): a directory whose newest backup is older
    # than the alert window means the producer has stopped; pruning while
    # production is down is how retention deletes the last good backup.
    if [[ -n "${newest_age}" && ${newest_age} -gt ${RETENTION_STALE_ALERT_DAYS} ]]; then
        log "ERROR" "STALE BACKUPS in ${dir}: newest is ${newest_age}d old (threshold ${RETENTION_STALE_ALERT_DAYS}d). Is the backup job running?"
        STALE_ALARM=true
    elif [[ -z "${newest_age}" && ${total} -eq 0 ]]; then
        log "ERROR" "NO BACKUPS in ${dir}. Is the backup job running?"
        STALE_ALARM=true
    fi
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local force=false

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
            --force)
                force=true
                shift
                ;;
            --dry-run)
                force=false
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

    log "INFO" "Retention policy: daily=${RETENTION_DAILY_DAYS}d, weekly=${RETENTION_WEEKLY_WEEKS}w, monthly=${RETENTION_MONTHLY_MONTHS}m, min-keep=${RETENTION_MIN_KEEP}"
    if [[ "${force}" == false ]]; then
        log "INFO" "DRY RUN (no --force): nothing will be deleted"
    fi

    # Process both directories independently.
    prune_directory "${BACKUP_LOCAL_DIR}" "${force}"
    prune_directory "${BACKUP_NAS_DIR}" "${force}"

    if [[ "${STALE_ALARM}" == true ]]; then
        log "ERROR" "Retention finished with a STALENESS ALARM (see above)"
        exit 1
    fi
    log "INFO" "Retention cleanup complete"
}

main "$@"
