#!/bin/bash
# Shekel Budget App -- Production Deployment Script
#
# Pulls the latest code from GitHub, builds the Docker image, restarts
# the application container, verifies health, and rolls back to the
# previous image if the health check fails.
#
# Migrations run automatically via entrypoint.sh on container start.
# No manual migration step is needed.
#
# Usage:
#     ./scripts/deploy.sh [OPTIONS]
#
# Options:
#     --skip-pull         Skip git pull (deploy from current working tree)
#     --skip-backup       Skip pre-deploy database backup
#     --health-timeout N  Seconds to wait for health check (default: 60)
#     --health-interval N Seconds between health check retries (default: 5)
#     --help              Show this help message
#
# Exit codes:
#     0   Deployment completed successfully
#     1   Fatal error (see log output for details)
#
# Prerequisites:
#     - Docker and Docker Compose installed
#     - Git repository cloned to DEPLOY_DIR
#     - .env file configured (see .env.example)
#     - NAS mounted if using backup before deploy

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
APP_CONTAINER="${APP_CONTAINER:-shekel-app}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"
NGINX_PORT="${NGINX_PORT:-80}"
HEALTH_URL="http://localhost:${NGINX_PORT}/health"

# Flags (overridden by command-line options).
SKIP_PULL=false
SKIP_BACKUP=false

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

Deploy the latest version of the Shekel budget app.

Workflow:
  1. Pull latest code from GitHub
  2. Back up the database (optional)
  3. Tag the current Docker image for rollback
  4. Build the new Docker image
  5. Restart the app container (migrations run automatically)
  6. Wait for the health endpoint to report healthy
  7. Roll back to the previous image if health check fails

Options:
    --skip-pull         Skip git pull (deploy from current working tree)
    --skip-backup       Skip pre-deploy database backup
    --health-timeout N  Seconds to wait for health check (default: 60)
    --health-interval N Seconds between health check retries (default: 5)
    --help              Show this help message

Environment Variables:
    DEPLOY_DIR          Repository root directory (default: script parent dir)
    APP_CONTAINER       App container name (default: shekel-app)
    HEALTH_TIMEOUT      Health check timeout in seconds (default: 60)
    HEALTH_INTERVAL     Health check retry interval in seconds (default: 5)
    NGINX_PORT          Nginx host port for health check URL (default: 80)
EOF
}

check_prerequisites() {
    # Verify required tools are available.
    local missing=false

    if ! command -v docker &>/dev/null; then
        log "ERROR" "docker command not found"
        missing=true
    fi

    if ! command -v git &>/dev/null; then
        log "ERROR" "git command not found"
        missing=true
    fi

    if ! command -v curl &>/dev/null; then
        log "ERROR" "curl command not found"
        missing=true
    fi

    if [ "$missing" = true ]; then
        log "ERROR" "Missing prerequisites. Install the required tools and retry."
        exit 1
    fi

    # Verify we're in a git repository.
    if [ ! -d "${DEPLOY_DIR}/.git" ]; then
        log "ERROR" "Not a git repository: ${DEPLOY_DIR}"
        exit 1
    fi

    # Verify .env file exists.
    if [ ! -f "${DEPLOY_DIR}/.env" ]; then
        log "ERROR" ".env file not found in ${DEPLOY_DIR}"
        log "ERROR" "Copy .env.example to .env and configure it before deploying."
        exit 1
    fi

    # Verify docker compose is available (v2 plugin syntax).
    if ! docker compose version &>/dev/null; then
        log "ERROR" "docker compose (v2) not found. Install the Docker Compose plugin."
        exit 1
    fi
}

pull_latest() {
    # Pull the latest code from the remote repository.
    log "INFO" "Pulling latest code from GitHub..."
    cd "${DEPLOY_DIR}"

    if ! git pull --ff-only; then
        log "ERROR" "git pull failed. Resolve conflicts or network issues and retry."
        exit 1
    fi

    log "INFO" "Code updated to $(git rev-parse --short HEAD)"
}

backup_database() {
    # Run a pre-deploy database backup.
    local backup_script="${DEPLOY_DIR}/scripts/backup.sh"

    if [ ! -x "$backup_script" ]; then
        log "WARNING" "Backup script not found or not executable: ${backup_script}"
        log "WARNING" "Skipping pre-deploy backup."
        return 0
    fi

    log "INFO" "Running pre-deploy database backup..."
    if ! "$backup_script"; then
        log "ERROR" "Pre-deploy backup failed. Aborting deployment."
        log "ERROR" "Fix the backup issue before deploying, or use --skip-backup."
        exit 1
    fi

    log "INFO" "Pre-deploy backup completed."
}

tag_previous_image() {
    # Tag the current running image as :previous for rollback.
    log "INFO" "Tagging current image for rollback..."

    # Get the image ID of the currently running app container.
    local current_image
    current_image=$(docker inspect --format='{{.Image}}' "${APP_CONTAINER}" 2>/dev/null || true)

    if [ -z "$current_image" ]; then
        log "WARNING" "No running ${APP_CONTAINER} container found. Skipping image tag."
        log "WARNING" "This is expected on first deployment."
        return 0
    fi

    # Tag it as :previous so we can restore it on rollback.
    docker tag "${current_image}" "${APP_CONTAINER}:previous"
    log "INFO" "Tagged current image as ${APP_CONTAINER}:previous"
}

build_image() {
    # Build the new Docker image.
    log "INFO" "Building new Docker image..."
    cd "${DEPLOY_DIR}"

    if ! docker compose build app; then
        log "ERROR" "Docker image build failed. The previous version is still running."
        exit 1
    fi

    log "INFO" "Docker image built successfully."
}

restart_app() {
    # Restart the app container with the new image.
    # Migrations run automatically via entrypoint.sh.
    log "INFO" "Restarting application container..."
    cd "${DEPLOY_DIR}"

    # Recreate the app container with the new image.
    # --no-deps: only restart the app, not db or nginx.
    # The db and nginx containers remain running.
    if ! docker compose up -d --no-deps --force-recreate app; then
        log "ERROR" "Failed to restart app container."
        rollback
        exit 1
    fi

    log "INFO" "App container restarted. Waiting for health check..."
}

wait_for_health() {
    # Poll the health endpoint until it reports healthy or times out.
    local elapsed=0
    local status

    log "INFO" "Checking health at ${HEALTH_URL} (timeout: ${HEALTH_TIMEOUT}s)..."

    while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
        # Attempt a health check. Suppress errors (container may still be starting).
        status=$(curl -sf "${HEALTH_URL}" 2>/dev/null || true)

        if [ -n "$status" ]; then
            # Parse the JSON response for the status field.
            local health_status
            health_status=$(echo "$status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)

            if [ "$health_status" = "healthy" ]; then
                log "INFO" "Health check passed: ${status}"
                return 0
            fi

            # Got a response but not healthy.
            log "WARNING" "Health check returned: ${status}"
        fi

        sleep "$HEALTH_INTERVAL"
        elapsed=$((elapsed + HEALTH_INTERVAL))
    done

    # Timed out.
    log "ERROR" "Health check timed out after ${HEALTH_TIMEOUT}s"

    # Capture container logs for debugging.
    log "ERROR" "Last 20 lines of app container logs:"
    docker logs --tail 20 "${APP_CONTAINER}" 2>&1 | while IFS= read -r line; do
        log "ERROR" "  ${line}"
    done

    return 1
}

rollback() {
    # Restore the previous image and restart the app container.
    log "WARNING" "=== ROLLING BACK ==="

    # Check if a previous image tag exists.
    if ! docker image inspect "${APP_CONTAINER}:previous" &>/dev/null; then
        log "ERROR" "No previous image found (${APP_CONTAINER}:previous). Cannot roll back."
        log "ERROR" "Manual intervention required."
        return 1
    fi

    cd "${DEPLOY_DIR}"

    # Stop the failed container.
    log "INFO" "Stopping failed container..."
    docker compose stop app 2>/dev/null || true

    # Re-tag the previous image as the current compose image.
    # Get the compose image name (what docker compose build targets).
    local compose_image
    compose_image=$(docker compose images app --format json 2>/dev/null \
        | python3 -c "import sys,json; data=json.load(sys.stdin); print(data[0].get('Repository','') if isinstance(data,list) else data.get('Repository',''))" 2>/dev/null || true)

    if [ -n "$compose_image" ]; then
        docker tag "${APP_CONTAINER}:previous" "${compose_image}:latest"
        log "INFO" "Restored previous image as ${compose_image}:latest"
    else
        log "WARNING" "Could not determine compose image name. Using direct container restart."
    fi

    # Restart with the previous image.
    docker compose up -d --no-deps --force-recreate app

    # Verify the rollback succeeded.
    log "INFO" "Verifying rollback health..."
    if wait_for_health; then
        log "WARNING" "Rollback successful. Previous version is running."
        log "WARNING" "Investigate the failed deployment before retrying."
    else
        log "ERROR" "Rollback FAILED. The application may be down."
        log "ERROR" "Manual intervention required. Check container logs:"
        log "ERROR" "  docker logs ${APP_CONTAINER}"
    fi
}

main() {
    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-pull)
                SKIP_PULL=true
                shift
                ;;
            --skip-backup)
                SKIP_BACKUP=true
                shift
                ;;
            --health-timeout)
                HEALTH_TIMEOUT="$2"
                shift 2
                ;;
            --health-interval)
                HEALTH_INTERVAL="$2"
                shift 2
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

    log "INFO" "=== Shekel Deployment Starting ==="
    log "INFO" "Deploy directory: ${DEPLOY_DIR}"

    # Step 1: Check prerequisites.
    check_prerequisites

    # Step 2: Pull latest code.
    if [ "$SKIP_PULL" = false ]; then
        pull_latest
    else
        log "INFO" "Skipping git pull (--skip-pull)."
    fi

    # Step 3: Pre-deploy backup.
    if [ "$SKIP_BACKUP" = false ]; then
        backup_database
    else
        log "INFO" "Skipping pre-deploy backup (--skip-backup)."
    fi

    # Step 4: Tag current image for rollback.
    tag_previous_image

    # Step 5: Build new image.
    build_image

    # Step 6: Restart app container.
    restart_app

    # Step 7: Health check.
    if wait_for_health; then
        log "INFO" "=== Deployment Successful ==="
        log "INFO" "Application is healthy at ${HEALTH_URL}"

        # Clean up the rollback image to save disk space.
        docker rmi "${APP_CONTAINER}:previous" 2>/dev/null || true
    else
        log "ERROR" "=== Deployment Failed ==="
        rollback
        exit 1
    fi
}

main "$@"
