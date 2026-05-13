#!/bin/bash
# Shekel Budget App -- Retire Pre-Rename Docker Resources
#
# One-shot operator helper that removes the legacy Docker resources
# left behind by the 2026-03-23 project rename (``shekel-*`` ->
# ``shekel-prod-*``).  Closes audit finding F-054.
#
# Targets (none of these are re-created by the current
# docker-compose.yml; they linger only because pre-rename
# ``restart: unless-stopped`` keeps containers alive across reboots):
#
#   Containers:  shekel-app, shekel-db, shekel-nginx
#   Networks:    shekel_backend, shekel_frontend, shekel_default
#   Volume:      shekel_pgdata    (DESTRUCTIVE -- backed up first)
#
# The pre-rename pgdata volume MAY contain real production data from
# the era before the rename.  This script always tarballs the volume
# to a backup directory under ${BACKUP_DIR} BEFORE removing it, and
# the tarball is left in place after the run completes -- the
# operator confirms it is intact and removes it manually when no
# longer needed.  This deliberate two-step posture exists because
# losing pre-rename data would be unrecoverable; an extra disk-space
# tradeoff is the right tradeoff.
#
# Usage:
#     ./scripts/retire_stale_containers.sh --dry-run
#     ./scripts/retire_stale_containers.sh --confirm
#     ./scripts/retire_stale_containers.sh --confirm --force
#     ./scripts/retire_stale_containers.sh --help
#
# Modes:
#     --dry-run   List stale resources without modifying anything.
#                 This is the default when no mode flag is given so a
#                 mistyped invocation cannot delete data.
#     --confirm   Actually remove the resources.  Without --force, the
#                 operator is prompted for an explicit ``yes`` before
#                 destruction begins.
#     --force     Skip the interactive prompt.  Only meaningful with
#                 --confirm.  Intended for non-interactive runs after
#                 the operator has already reviewed the plan via a
#                 prior --dry-run.
#
# Configuration via env vars:
#     BACKUP_DIR  Where the pgdata tarball is written.  Default:
#                 /var/backups/shekel/stale-volumes
#
# Exit codes:
#     0   Success (or --dry-run with nothing to do)
#     1   Generic error (invalid args, docker unavailable)
#     2   --confirm cancelled by the operator at the prompt

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
STALE_CONTAINERS=(
    "shekel-app"
    "shekel-db"
    "shekel-nginx"
)
STALE_NETWORKS=(
    "shekel_backend"
    "shekel_frontend"
    "shekel_default"
)
# The volume removal step destroys data; the script always backs it
# up to BACKUP_DIR before unlinking.  Listed separately from
# containers/networks because the destruction path differs.
STALE_VOLUMES=(
    "shekel_pgdata"
)
BACKUP_DIR="${BACKUP_DIR:-/var/backups/shekel/stale-volumes}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# ── Logging ──────────────────────────────────────────────────────
# Mirrors the format used by scripts/backup.sh so operators reading
# both logs see the same shape.
log() {
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# //; s/^#$//'
}

# ── Argument parsing ─────────────────────────────────────────────
MODE="dry-run"
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            MODE="dry-run"
            shift
            ;;
        --confirm)
            MODE="confirm"
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            log "ERROR" "Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

# ── Prerequisite checks ──────────────────────────────────────────
check_prerequisites() {
    if ! command -v docker >/dev/null 2>&1; then
        log "ERROR" "docker command not found on PATH"
        exit 1
    fi
    # docker info exits non-zero when the daemon is unreachable.
    if ! docker info >/dev/null 2>&1; then
        log "ERROR" "docker daemon is not reachable (try: systemctl start docker)"
        exit 1
    fi
    # The script destroys volumes; the backup directory must be
    # writable BEFORE we touch any container.  Fail-closed is the
    # right posture -- a backup that we cannot write defeats the
    # whole "tar before unlink" guard.
    if [[ "${MODE}" == "confirm" ]]; then
        if ! mkdir -p "${BACKUP_DIR}" 2>/dev/null; then
            log "ERROR" "BACKUP_DIR (${BACKUP_DIR}) cannot be created"
            log "ERROR" "Override with: BACKUP_DIR=/path ./retire_stale_containers.sh ..."
            exit 1
        fi
        if ! touch "${BACKUP_DIR}/.write-test" 2>/dev/null; then
            log "ERROR" "BACKUP_DIR (${BACKUP_DIR}) is not writable"
            exit 1
        fi
        rm -f "${BACKUP_DIR}/.write-test"
    fi
}

# ── Discovery ────────────────────────────────────────────────────
# Returns 0 (success) when the named resource exists, 1 otherwise.
# docker ps/network/volume use ``--quiet`` so the function only has
# to check exit status.  We use ``--filter`` rather than ``--format``
# + grep so a name substring (e.g. "shekel-app" matching
# "shekel-app-1") cannot produce a false positive.
container_exists() {
    docker ps -a --filter "name=^${1}$" --quiet | grep -q . 2>/dev/null
}
network_exists() {
    docker network ls --filter "name=^${1}$" --quiet | grep -q . 2>/dev/null
}
volume_exists() {
    docker volume ls --filter "name=^${1}$" --quiet | grep -q . 2>/dev/null
}

# ── Listing ──────────────────────────────────────────────────────
# Prints what the script would do.  Always runs BEFORE any
# destructive action so the prompt the operator sees matches what
# will actually be removed.  Exits 0 when no stale resources are
# found so the caller can use the exit code as "nothing to do".
list_targets() {
    local found=0
    log "INFO" "Scanning for stale pre-rename Docker resources..."
    log "INFO" ""
    log "INFO" "Containers (would be stopped and removed):"
    for c in "${STALE_CONTAINERS[@]}"; do
        if container_exists "${c}"; then
            local status
            status=$(docker inspect "${c}" --format '{{.State.Status}}' 2>/dev/null || echo "unknown")
            log "INFO" "  - ${c} (${status})"
            found=1
        fi
    done
    log "INFO" ""
    log "INFO" "Networks (would be removed):"
    for n in "${STALE_NETWORKS[@]}"; do
        if network_exists "${n}"; then
            log "INFO" "  - ${n}"
            found=1
        fi
    done
    log "INFO" ""
    log "INFO" "Volumes (would be tarballed to ${BACKUP_DIR} BEFORE removal):"
    for v in "${STALE_VOLUMES[@]}"; do
        if volume_exists "${v}"; then
            local size
            # The volume is backed by /var/lib/docker/volumes/<name>/_data
            # in the default driver; falling back to "unknown" when
            # the path is not readable (e.g. when the script is run
            # without root and the docker daemon stores the volume
            # under root-owned paths -- the operator can still read
            # the tarball after backup).
            size=$(docker run --rm -v "${v}:/data:ro" alpine du -sh /data 2>/dev/null | awk '{print $1}' || echo "unknown")
            log "INFO" "  - ${v} (size: ${size})"
            found=1
        fi
    done
    log "INFO" ""
    if [[ "${found}" -eq 0 ]]; then
        log "INFO" "No stale resources found.  Nothing to do."
        exit 0
    fi
}

# ── Confirmation prompt ──────────────────────────────────────────
# Required before any destructive action when --force is absent.
# The prompt requires the literal string ``yes`` -- typing ``y`` or
# pressing enter declines.  This pattern matches ``docker compose
# down -v`` style confirmation behavior.
prompt_confirm() {
    if [[ "${FORCE}" -eq 1 ]]; then
        log "INFO" "Skipping confirmation prompt (--force)"
        return 0
    fi
    echo ""
    echo "===================================================="
    echo "  About to REMOVE the resources listed above."
    echo "  Volume contents will be tarballed to ${BACKUP_DIR}"
    echo "  BEFORE the volume is unlinked."
    echo "===================================================="
    echo ""
    # ``read -r`` to avoid backslash interpretation; ``< /dev/tty`` so
    # the prompt works even when stdin is a pipe (e.g. wrapped in
    # ``echo yes | ./retire_stale_containers.sh --confirm`` -- which
    # is intentionally NOT supported; --force is the documented
    # non-interactive path).
    local response
    read -r -p "Type 'yes' to proceed: " response < /dev/tty
    if [[ "${response}" != "yes" ]]; then
        log "INFO" "Cancelled by operator (response was '${response}', expected 'yes')"
        exit 2
    fi
}

# ── Destructive actions ──────────────────────────────────────────
# Each removal step is idempotent: missing resources are skipped
# silently rather than failing.  The order is: stop+remove
# containers (so they release their volume mounts), back up volumes,
# remove volumes, remove networks (containers must be gone before
# their networks can be removed).
backup_volume() {
    local vol="$1"
    if ! volume_exists "${vol}"; then
        return 0
    fi
    local tarball="${BACKUP_DIR}/${vol}-${TIMESTAMP}.tar.gz"
    log "INFO" "Backing up volume ${vol} -> ${tarball}"
    # Run an ephemeral alpine container with the volume mounted
    # read-only and the backup directory mounted writable.  ``-C /``
    # so the tarball stores ``data/...`` rather than absolute paths.
    docker run --rm \
        -v "${vol}:/data:ro" \
        -v "${BACKUP_DIR}:/backup" \
        alpine \
        tar czf "/backup/$(basename "${tarball}")" -C / data
    if [[ ! -s "${tarball}" ]]; then
        log "ERROR" "Backup tarball ${tarball} is empty or missing"
        exit 1
    fi
    local size
    size=$(du -h "${tarball}" | awk '{print $1}')
    log "INFO" "Backup complete: ${tarball} (${size})"
}

remove_container() {
    local c="$1"
    if ! container_exists "${c}"; then
        return 0
    fi
    log "INFO" "Stopping container ${c}..."
    # ``|| true`` so an already-stopped container does not abort the
    # script under set -e.  ``-t 10`` gives PostgreSQL a graceful
    # window to flush WAL on shutdown -- pre-rename pgdata may not
    # be the source of truth, but it could still be useful for a
    # forensic pgdump later.
    docker stop -t 10 "${c}" >/dev/null 2>&1 || true
    log "INFO" "Removing container ${c}..."
    docker rm -f "${c}" >/dev/null
}

remove_network() {
    local n="$1"
    if ! network_exists "${n}"; then
        return 0
    fi
    log "INFO" "Removing network ${n}..."
    # ``docker network rm`` fails if any container is still attached;
    # the container removal step above must run first.  The error
    # surface here is small -- if a network removal fails the
    # operator will see the docker error and can re-run after
    # detaching the offending container.
    docker network rm "${n}" >/dev/null
}

remove_volume() {
    local v="$1"
    if ! volume_exists "${v}"; then
        return 0
    fi
    log "INFO" "Removing volume ${v}..."
    docker volume rm "${v}" >/dev/null
}

# ── Main ─────────────────────────────────────────────────────────
main() {
    check_prerequisites
    list_targets

    if [[ "${MODE}" == "dry-run" ]]; then
        log "INFO" ""
        log "INFO" "Dry run.  Nothing was modified."
        log "INFO" "Re-run with --confirm to apply."
        exit 0
    fi

    prompt_confirm

    log "INFO" ""
    log "INFO" "Backing up volumes (one tarball per volume)..."
    for v in "${STALE_VOLUMES[@]}"; do
        backup_volume "${v}"
    done

    log "INFO" ""
    log "INFO" "Removing containers..."
    for c in "${STALE_CONTAINERS[@]}"; do
        remove_container "${c}"
    done

    log "INFO" ""
    log "INFO" "Removing volumes..."
    for v in "${STALE_VOLUMES[@]}"; do
        remove_volume "${v}"
    done

    log "INFO" ""
    log "INFO" "Removing networks..."
    for n in "${STALE_NETWORKS[@]}"; do
        remove_network "${n}"
    done

    log "INFO" ""
    log "INFO" "Stale resource cleanup complete."
    log "INFO" "Backup tarballs remain in ${BACKUP_DIR} for operator review."
}

main "$@"
