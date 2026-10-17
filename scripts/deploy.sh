#!/bin/bash
# Shekel Budget App -- SELF-HOSTED Source-Build Deployment Script
#
# SCOPE (polyglot audit 2026-06-12, OPS/SH-14): this script serves
# self-hosted installs that BUILD FROM SOURCE (docker-compose.yml +
# docker-compose.build.yml). It is NOT the maintainer's production
# deploy path -- that is the digest-pinned, cosign-keyless-verified
# /opt/docker/scripts/shekel-deploy.sh pipeline driving the hardened
# stack at /opt/docker/shekel. Running this script on a host where
# that canonical stack exists would replace the pinned, hardened
# container with an unpinned locally-built one, so it now REFUSES to
# run when /opt/docker/shekel exists (override: --force-self-hosted).
#
# Pulls the latest code from GitHub, builds the Docker image, restarts
# the application container, verifies health, and rolls back to the
# previous image if the health check fails.
#
# Migrations run automatically via entrypoint.sh on container start.
# No manual migration step is needed.
#
# Options and settings, each setting with its default: ./scripts/deploy.sh --help
#
# Exit codes:
#     0   Deployment completed successfully
#     1   Fatal error (see log output for details)
#
# Prerequisites:
#     - Docker and Docker Compose installed
#     - This script run from the git checkout to deploy: it deploys the
#       checkout it is in
#     - .env file configured (see .env.example)
#     - The stack running (docker compose -f docker-compose.yml
#       -f docker-compose.build.yml up -d): the health check reaches the app
#       through nginx, at the address compose published it on
#     - NAS mounted if using backup before deploy
#     - The backup key file (docs/backup_runbook.md, section 3.3), unless
#       --skip-backup: the pre-deploy backup refuses without it, and a
#       refused or missing backup stops the deploy before the app is touched

set -euo pipefail

# The docker facts the shell scripts share -- an image digest's shape -- have
# one home beside this script (plan step balance:X-do).  A copy of it lacking
# a function read below is refused here, before anything is pulled, built or
# restarted.
_docker_lib="$(dirname "${BASH_SOURCE[0]}")/_docker_lib.sh"
# shellcheck source=_docker_lib.sh
if ! {
    source "${_docker_lib}" \
        && declare -F image_digest_or_empty >/dev/null
}; then
    echo "ERROR: cannot load ${_docker_lib}, or it lacks a function this script" \
        "reads (image_digest_or_empty).  It must come from this script's" \
        "revision." >&2
    exit 1
fi

# The settings the shell scripts read, each default's one home, and the
# checkout this script deploys (SHEKEL_REPO_ROOT, the checkout this script and
# its libs are in; plan step balance:X-do, finding BAL-664).
# shellcheck source=_settings_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_settings_lib.sh"

# ── Configuration ────────────────────────────────────────────────
# Each read from the environment of the shell that runs this script (never
# .env, which is compose's file; check_prerequisites only confirms it
# exists), else its default in _settings_lib.sh.
read_settings \
    APP_CONTAINER "App container name" \
    HEALTH_TIMEOUT "Seconds to wait for the health check (--health-timeout sets it)" \
    HEALTH_INTERVAL "Seconds between health check retries (--health-interval sets it)" \
    COSIGN_PUBLIC_KEY "Verifier key the new image is verified with before the swap" \
    COSIGN_PRIVATE_KEY "Signing key, kept outside the repository (chmod 600); signing needs both keys" \
    COSIGN_REQUIRED "When 'true', a missing cosign or key fails the deploy (--require-cosign)"

# Array, not a space-joined string: the callers expand it unquoted
# otherwise -- the precise unquoted-expansion pattern the shell
# standards ban (OPS/SH-19).
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.build.yml)
# The port nginx listens on INSIDE its container: a COPY of the right-hand
# side of the nginx service's ``ports:`` mapping in docker-compose.yml and of
# deploy/nginx-bundled/nginx.conf's ``listen``.  If they part, compose answers
# "no port" and the deploy refuses (plan step X-ec owns their one home).  The
# HOST port is compose's to resolve (from .env), so it is asked, never spelled
# here (plan step balance:X-do, finding BAL-661); resolve_health_url builds
# HEALTH_URL from compose's answer before anything is touched.
NGINX_CONTAINER_PORT=8080
HEALTH_URL=""

# IMAGE_REF, the reference cosign signs and verifies and rollback restores,
# is no setting: it is compose's name for the app service's image, the tag
# its build writes and the name its up starts the app from, and
# resolve_image_ref asks compose for it before anything is touched (plan step
# balance:X-do, finding BAL-664).  Any other name would sign and verify one
# image while the deploy started another.  It does not make up run the image
# the build wrote: docker-compose.yml's pull_policy: always has up pull that
# name from the registry first, and a pull that succeeds replaces the local
# build under it (finding BAL-670).  Cosign signs it at the digest the local
# build produced; the digest is printed for the operator to paste into
# /opt/docker/shekel/.env (SHEKEL_IMAGE_DIGEST).

# Cosign integration (audit findings F-060, F-155 / Commit C-36): signing
# needs cosign and both keys, verifying cosign and COSIGN_PUBLIC_KEY.  A step
# missing what it needs warns and is skipped, so a fresh checkout without
# cosign or a generated keypair still completes a first deploy, unless
# COSIGN_REQUIRED (or --require-cosign) makes it fail.  A verifier key with
# no signing key skips the signing and then fails the verify: the deploy
# refuses the unsigned image.

# Flags (overridden by command-line options).
SKIP_PULL=false
SKIP_BACKUP=false
SKIP_COSIGN=false
FORCE_SELF_HOSTED=false

# ── Functions ────────────────────────────────────────────────────

log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    # shellcheck disable=SC2312 # date with a fixed format string always succeeds; this is log-line formatting, not a checked command.
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy the latest version of the Shekel budget app: the checkout this script
is in, ${SHEKEL_REPO_ROOT}.

Workflow:
  0. Ask compose where nginx is published and what it names the app image
     (refused when it answers either with nothing)
  1. Pull latest code from GitHub
  2. Back up the database (optional)
  3. Tag the current Docker image for rollback
  4. Build the new Docker image
  5. Sign the new image with Cosign (audit Commit C-36)
  6. Verify the signature before swapping the running container
  7. Restart the app container (migrations run automatically)
  8. Wait for the health endpoint to report healthy
  9. Roll back to the previous image if health check fails

Options:
    --skip-pull         Skip git pull (deploy from current working tree)
    --skip-backup       Skip pre-deploy database backup
    --skip-cosign       Skip Cosign sign + verify (use only in emergencies)
    --require-cosign    Fail the deploy when cosign/keys are missing
                        (equivalent to COSIGN_REQUIRED=true)
    --force-self-hosted Run even though /opt/docker/shekel exists (see header)
    --health-timeout N  Seconds to wait for health check (sets HEALTH_TIMEOUT)
    --health-interval N Seconds between health check retries (sets HEALTH_INTERVAL)
    --help              Show this help message

Cosign itself reads COSIGN_PASSWORD, the signing key's passphrase when it
has one; this script only passes it on.

EOF
    print_settings
}

refuse_retired_settings() {
    # DEPLOY_DIR and IMAGE_REF are no settings any more (plan step
    # balance:X-do, finding BAL-664).  Set at all -- empty included -- each
    # is refused before anything else, naming what replaced it: an install
    # still setting one would otherwise believe it chose what this script
    # now takes from elsewhere.
    local refused=false
    if [[ -n "${DEPLOY_DIR+x}" ]]; then
        log "ERROR" "DEPLOY_DIR is set, but this script no longer reads it: it deploys the"
        log "ERROR" "checkout it is in, ${SHEKEL_REPO_ROOT}.  To deploy another checkout,"
        log "ERROR" "run that checkout's own scripts/deploy.sh."
        log "ERROR" "Remove DEPLOY_DIR from wherever it is set (a shell profile, a wrapper)."
        refused=true
    fi
    if [[ -n "${IMAGE_REF+x}" ]]; then
        log "ERROR" "IMAGE_REF is set, but this script no longer reads it: cosign signs and"
        log "ERROR" "verifies the image compose names for the app service."
        log "ERROR" "Remove IMAGE_REF from wherever it is set (a shell profile, a wrapper)."
        refused=true
    fi
    if [[ "${refused}" == true ]]; then
        exit 1
    fi
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

    # resolve_image_ref reads compose's answer with it, before anything is
    # touched.
    if ! command -v python3 &>/dev/null; then
        log "ERROR" "python3 command not found"
        missing=true
    fi

    if [ "$missing" = true ]; then
        log "ERROR" "Missing prerequisites. Install the required tools and retry."
        exit 1
    fi

    # Verify we're in a git repository.
    if [ ! -d "${SHEKEL_REPO_ROOT}/.git" ]; then
        log "ERROR" "Not a git repository: ${SHEKEL_REPO_ROOT}"
        exit 1
    fi

    # Verify .env file exists.
    if [ ! -f "${SHEKEL_REPO_ROOT}/.env" ]; then
        log "ERROR" ".env file not found in ${SHEKEL_REPO_ROOT}"
        log "ERROR" "Copy .env.example to .env and configure it before deploying."
        exit 1
    fi

    # Verify docker compose is available (v2 plugin syntax).
    if ! docker compose version &>/dev/null; then
        log "ERROR" "docker compose (v2) not found. Install the Docker Compose plugin."
        exit 1
    fi
}

nginx_health_url() {
    # Print the URL of /health as compose published nginx on this host, or
    # NOTHING.  Compose's answer, measured on compose 5.6.0 (2026-10-16), is
    # ONE line, the IPv4 binding <host-ip>:<port>, or exit 1 with its reason
    # when nginx never started, is stopped, or does not publish the port.
    # Only that one-line shape is read: the any-address 0.0.0.0 becomes
    # localhost, a bound IP is kept, and any other answer prints nothing and
    # is shown on stderr (compose's own reason for an exit 1 reaches stderr
    # unread).  Returns 0 either way: its caller assigns the answer under
    # errexit and refuses an empty one by name, so a non-zero return would
    # end the script at that assignment, before the refusal.
    local address
    if ! address=$(docker compose "${COMPOSE_FILES[@]}" port nginx "${NGINX_CONTAINER_PORT}"); then
        return 0
    fi
    local ipv4='([0-9]{1,3}\.){3}[0-9]{1,3}'
    if [[ ! "${address}" =~ ^(${ipv4}):([0-9]{1,5})$ ]]; then
        log "ERROR" "Compose answered '${address}', not one IPv4 <address>:<port> line." >&2
        return 0
    fi
    local host="${BASH_REMATCH[1]}"
    local port="${BASH_REMATCH[3]}"
    if [[ "${host}" == "0.0.0.0" ]]; then
        host=localhost
    fi
    printf 'http://%s:%s/health\n' "${host}" "${port}"
}

resolve_health_url() {
    # Set HEALTH_URL from compose's published address for nginx, or refuse.
    # It runs before anything is touched: both of this script's ``up -d``
    # lines (restart_app's and rollback's) are ``--no-deps --force-recreate
    # app``, so no deploy starts or recreates nginx and its binding is the
    # same before the swap and after it.  A stopped nginx is refused here,
    # with nothing pulled, backed up, built or swapped.
    cd "${SHEKEL_REPO_ROOT}"
    HEALTH_URL=$(nginx_health_url)
    if [[ -z "${HEALTH_URL}" ]]; then
        log "ERROR" "No published IPv4 address for nginx's port ${NGINX_CONTAINER_PORT} (compose's answer is above)."
        log "ERROR" "The health check reaches the app through nginx: start the stack"
        log "ERROR" "(docker compose ${COMPOSE_FILES[*]} up -d) and retry."
        exit 1
    fi
    log "INFO" "Health check URL: ${HEALTH_URL} (nginx's published address)"
}

resolve_image_ref() {
    # Set IMAGE_REF to compose's name for the app service's image, or refuse
    # before anything is touched.  `config --format json app` renders the
    # app service and the ones it depends on over the same files and .env the
    # build and up read, and only the app's image is taken: `config --images
    # app` would list the others' images too (both measured on compose 5.6.0,
    # 2026-10-17).  Compose's own refusal (a required .env variable missing,
    # say) reaches stderr; an answer that is not that shape, or names no
    # image, is refused here.
    local config
    cd "${SHEKEL_REPO_ROOT}"
    IMAGE_REF=""
    if config=$(docker compose "${COMPOSE_FILES[@]}" config --format json app); then
        IMAGE_REF=$(python3 -c '
import json, sys
try:
    image = json.load(sys.stdin)["services"]["app"]["image"]
except (ValueError, KeyError, TypeError):
    sys.exit(0)
if isinstance(image, str):
    print(image)
' <<<"${config}")
    fi
    if [[ -z "${IMAGE_REF}" ]]; then
        log "ERROR" "Compose names no image for the app service: docker compose"
        log "ERROR" "${COMPOSE_FILES[*]} config --format json app refused (its reason is"
        log "ERROR" "above) or its answer holds no services.app.image.  Cosign signs, and"
        log "ERROR" "rollback restores, the image compose names: fix the compose files or"
        log "ERROR" ".env and retry."
        exit 1
    fi
    log "INFO" "App image: ${IMAGE_REF} (compose's name for the app service's image)"
}

pull_latest() {
    # Pull the latest code from the remote repository.
    log "INFO" "Pulling latest code from GitHub..."
    cd "${SHEKEL_REPO_ROOT}"

    if ! git pull --ff-only; then
        log "ERROR" "git pull failed. Resolve conflicts or network issues and retry."
        exit 1
    fi

    # shellcheck disable=SC2312 # HEAD is resolvable after the preceding ff-only pull succeeded; the short hash is used only for the display log line.
    log "INFO" "Code updated to $(git rev-parse --short HEAD)"
}

backup_database() {
    # Run a pre-deploy database backup, or stop the deploy before the app is
    # touched: a backup script that cannot run is refused like one that
    # refuses (Josh's ruling R-BAL304; --skip-backup is the explicit choice).
    local backup_script="${SHEKEL_REPO_ROOT}/scripts/backup.sh"

    if [ ! -x "$backup_script" ]; then
        log "ERROR" "Backup script not found or not executable: ${backup_script}"
        log "ERROR" "The app was not touched.  Restore the script, or use --skip-backup."
        exit 1
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
    cd "${SHEKEL_REPO_ROOT}"

    if ! docker compose "${COMPOSE_FILES[@]}" build app; then
        log "ERROR" "Docker image build failed. The previous version is still running."
        exit 1
    fi

    log "INFO" "Docker image built successfully."
}

cosign_available() {
    # Return 0 when cosign is on PATH, 1 otherwise.  Used by the
    # sign/verify wrappers below to decide whether to enforce or
    # downgrade to a warning.
    command -v cosign &>/dev/null
}

cosign_signing_key_present() {
    # Return 0 when both the private (signing) key and public
    # (verifier) key files exist and are readable.  We test both
    # because signing without a matching verifier means the next
    # deploy cannot verify; better to fail fast on the operator
    # config gap than emit an unverifiable signature.
    [ -f "${COSIGN_PRIVATE_KEY}" ] && [ -r "${COSIGN_PRIVATE_KEY}" ] \
        && [ -f "${COSIGN_PUBLIC_KEY}" ] && [ -r "${COSIGN_PUBLIC_KEY}" ]
}

cosign_verifier_key_present() {
    # Return 0 when only the verifier (public) key exists -- enough
    # for the verify step (which never reads the private key).
    [ -f "${COSIGN_PUBLIC_KEY}" ] && [ -r "${COSIGN_PUBLIC_KEY}" ]
}

cosign_resolve_digest() {
    # Print the local image's ID (sha256:...) to stdout, or empty on
    # failure.  ``docker inspect`` reads ``{{.Id}}`` only, never the
    # image's RepoDigests; the sign and verify steps below pass that ID
    # to cosign as ``${IMAGE_REF}@<ID>``.
    #
    # The first line by parameter expansion, not `| head -n 1` (plan step
    # balance:X-dm): no reader can leave the pipe early, and `|| true`
    # makes a failed inspect EMPTY here by construction, not because the
    # callers' command substitution happens to run without errexit.
    #
    # The answer is a WHOLE digest or empty (plan step balance:X-do, finding
    # BAL-623), as the shared helper spells the shape: docker may print the
    # ID without its algorithm on some versions, so a bare one is read with
    # the helper's IMAGE_DIGEST_PREFIX put back, and an ID that is still not
    # a whole digest answers empty.
    local id digest
    id=$(docker inspect --format='{{.Id}}' "${IMAGE_REF}" 2>/dev/null || true)
    id=${id%%$'\n'*}
    digest=$(image_digest_or_empty "$id")
    if [ -z "$digest" ]; then
        digest=$(image_digest_or_empty "${IMAGE_DIGEST_PREFIX}${id}")
    fi
    echo "$digest"
}

handle_cosign_skip() {
    # Centralised "no cosign / no key" handler so sign and verify
    # apply the same fail-vs-warn policy.  Emits a remediation
    # pointer and respects COSIGN_REQUIRED + --skip-cosign.
    local reason="$1"

    if [ "${SKIP_COSIGN}" = true ]; then
        log "WARNING" "${reason}; --skip-cosign was passed, continuing."
        return 0
    fi

    if [ "${COSIGN_REQUIRED}" = "true" ]; then
        log "ERROR" "${reason}; COSIGN_REQUIRED=true."
        log "ERROR" "Install cosign and generate a keypair (see"
        log "ERROR" "deploy/README.md \"Image digest pinning\"), or"
        log "ERROR" "rerun with --skip-cosign to bypass for this deploy."
        return 1
    fi

    log "WARNING" "${reason}; continuing without signature controls."
    log "WARNING" "Export COSIGN_REQUIRED=true or pass --require-cosign"
    log "WARNING" "to promote this warning to an error in steady-state ops."
    # (--require-cosign exists since the OPS/SH-18 fix; it simply sets
    # COSIGN_REQUIRED=true for this run.)
    return 0
}

sign_image() {
    # Sign the locally-built image with the maintainer's Cosign
    # keypair so the verify step (and any downstream operator) can
    # confirm the running image matches what was built on this host.
    # Audit finding F-155 / Commit C-36.
    log "INFO" "Signing image with Cosign..."

    # shellcheck disable=SC2310 # cosign_available is a boolean predicate (wraps command -v); its 0/1 is exactly what this if tests, no internal command needs to abort the script.
    if ! cosign_available; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status, nothing is swallowed.
        handle_cosign_skip "cosign not installed (sign step)" || exit 1
        return 0
    fi

    # shellcheck disable=SC2310 # cosign_signing_key_present is a boolean predicate ([ -f ]/[ -r ] tests); its 0/1 is exactly what this if tests.
    if ! cosign_signing_key_present; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status.
        handle_cosign_skip \
            "cosign keypair missing at COSIGN_PRIVATE_KEY=${COSIGN_PRIVATE_KEY} or COSIGN_PUBLIC_KEY=${COSIGN_PUBLIC_KEY}" \
            || exit 1
        return 0
    fi

    local digest
    digest=$(cosign_resolve_digest)
    if [ -z "$digest" ]; then
        log "ERROR" "Could not resolve digest for ${IMAGE_REF}; cannot sign."
        log "ERROR" "Verify 'docker images ${IMAGE_REF}' lists the image."
        exit 1
    fi

    # Sign by digest (cosign best practice -- signing by tag would
    # let a tag swap point the signature at a different image
    # silently).  COSIGN_PASSWORD must be exported by the operator
    # before invoking deploy.sh; the script never prompts because
    # the deploy is intended to run unattended once initiated.
    if ! COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign sign --yes \
        --key "${COSIGN_PRIVATE_KEY}" \
        "${IMAGE_REF}@${digest}"; then
        log "ERROR" "cosign sign failed for ${IMAGE_REF}@${digest}."
        log "ERROR" "Check COSIGN_PASSWORD env var (required for"
        log "ERROR" "encrypted private keys) and the key permissions."
        exit 1
    fi

    log "INFO" "Image signed: ${IMAGE_REF}@${digest}"
    log "INFO" "Update SHEKEL_IMAGE_DIGEST=${digest} in /opt/docker/shekel/.env"
    log "INFO" "(see deploy/README.md \"Image digest pinning\")."
}

verify_image_signature() {
    # Verify the signature on the image about to be deployed.  Runs
    # AFTER sign_image so the verifier exercises the just-produced
    # signature -- catches a signing-key/verifier-key mismatch
    # before the container swap.  Audit finding F-155 / Commit C-36.
    log "INFO" "Verifying image signature with Cosign..."

    # shellcheck disable=SC2310 # cosign_available is a boolean predicate (wraps command -v); its 0/1 is exactly what this if tests, no internal command needs to abort the script.
    if ! cosign_available; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status, nothing is swallowed.
        handle_cosign_skip "cosign not installed (verify step)" || exit 1
        return 0
    fi

    # shellcheck disable=SC2310 # cosign_verifier_key_present is a boolean predicate ([ -f ]/[ -r ] tests); its 0/1 is exactly what this if tests.
    if ! cosign_verifier_key_present; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status.
        handle_cosign_skip \
            "cosign verifier key missing at COSIGN_PUBLIC_KEY=${COSIGN_PUBLIC_KEY}" \
            || exit 1
        return 0
    fi

    local digest
    digest=$(cosign_resolve_digest)
    if [ -z "$digest" ]; then
        log "ERROR" "Could not resolve digest for ${IMAGE_REF}; cannot verify."
        exit 1
    fi

    if ! cosign verify --key "${COSIGN_PUBLIC_KEY}" \
        "${IMAGE_REF}@${digest}" >/dev/null 2>&1; then
        log "ERROR" "cosign verify FAILED for ${IMAGE_REF}@${digest}."
        log "ERROR" "The locally-built image is unsigned or its signature"
        log "ERROR" "does not match COSIGN_PUBLIC_KEY=${COSIGN_PUBLIC_KEY}."
        log "ERROR" "Refusing to deploy an unverifiable image."
        exit 1
    fi

    log "INFO" "Signature verified: ${IMAGE_REF}@${digest}"
}

restart_app() {
    # Restart the app container with the new image.
    # Migrations run automatically via entrypoint.sh.
    log "INFO" "Restarting application container..."
    cd "${SHEKEL_REPO_ROOT}"

    # Recreate the app container with the new image.
    # --no-deps: only restart the app, not db or nginx.
    # The db and nginx containers remain running.
    if ! docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --force-recreate app; then
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

    cd "${SHEKEL_REPO_ROOT}"

    # Stop the failed container.
    log "INFO" "Stopping failed container..."
    docker compose "${COMPOSE_FILES[@]}" stop app 2>/dev/null || true

    # Put the previous image back under the name compose starts the app from
    # (resolve_image_ref).  The recreate below starts it only when compose
    # does not pull over that name first, which docker-compose.yml's
    # pull_policy: always asks it to (finding BAL-670).
    docker tag "${APP_CONTAINER}:previous" "${IMAGE_REF}"
    log "INFO" "Restored previous image as ${IMAGE_REF}"

    # Restart with the previous image.
    docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --force-recreate app

    # Verify the rollback succeeded.
    log "INFO" "Verifying rollback health..."
    # shellcheck disable=SC2310 # wait_for_health is a poll-with-timeout predicate returning 0 healthy / 1 timed-out; this if branches on that status and the timeout case is the handled (else) path.
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
    refuse_retired_settings

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
            --skip-cosign)
                SKIP_COSIGN=true
                shift
                ;;
            --require-cosign)
                # Promote missing-cosign warnings to hard errors for this
                # run (the option this script's own guidance recommended
                # but never parsed -- OPS/SH-18).
                COSIGN_REQUIRED=true
                shift
                ;;
            --force-self-hosted)
                FORCE_SELF_HOSTED=true
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
    log "INFO" "Deploying the checkout ${SHEKEL_REPO_ROOT}"

    # Canonical-stack guard (OPS/SH-14): on the maintainer host the
    # digest-pinned hardened stack lives at /opt/docker/shekel and is
    # deployed by shekel-deploy; this source-build script targets the SAME
    # compose project name and would silently swap the pinned container
    # for an unpinned local build.
    if [ -d /opt/docker/shekel ] && [ "${FORCE_SELF_HOSTED}" = false ]; then
        log "ERROR" "Canonical production stack detected at /opt/docker/shekel."
        log "ERROR" "Use the digest-pinned pipeline (shekel-deploy) to deploy there."
        log "ERROR" "If you REALLY mean to run a source-build deploy on this host,"
        log "ERROR" "rerun with --force-self-hosted."
        exit 1
    fi

    # Step 1: Check prerequisites, and resolve where nginx publishes the
    # health endpoint and what compose names the app image (each refused
    # here, before anything is touched).
    check_prerequisites
    resolve_health_url
    resolve_image_ref

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

    # Step 6: Sign the new image with Cosign (audit C-36 / F-155).
    # The sign step runs BEFORE restart_app so a signing failure
    # aborts the deploy with the previous version still serving
    # traffic; verify_image_signature then exercises the just-
    # produced signature so a key/verifier mismatch surfaces here
    # rather than after the swap.
    sign_image

    # Step 7: Verify the signature on the image about to deploy.
    verify_image_signature

    # Step 8: Restart app container.
    restart_app

    # Step 9: Health check.
    # shellcheck disable=SC2310 # wait_for_health is a poll-with-timeout predicate returning 0 healthy / 1 timed-out; this if branches on that status and the failure case triggers rollback in the else path.
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
