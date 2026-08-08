#!/usr/bin/env bash
# shekel-deploy.sh -- pull, verify, and roll shekel-prod-app to a new image.
#
# THIS FILE IS THE CANONICAL COPY.  It is installed by copying it to
# /opt/docker/scripts/shekel-deploy.sh, which /usr/local/bin/shekel-deploy
# symlinks.  It lived ONLY on the host until 2026-08-08 (plan finding F-14):
# the one path that rolls production was untracked, unreviewed, and outside
# every linter in the polyglot gate, which is why finding F-8 -- a rollback
# that cannot work for a migration-bearing release -- survived unnoticed.
# Keep the two in sync; `diff deploy/shekel-deploy.sh
# /opt/docker/scripts/shekel-deploy.sh` must be empty.
#
# KNOWN DEFECT, ruled and scheduled (plan step R-F8, ruling R-R14): the
# rollback below is unsafe for any release carrying a migration.  Migrations
# run at entrypoint step 3, BEFORE the health check, so a failed deploy leaves
# the database stamped where the previously-pinned image cannot follow; that
# image then dies at step 3 too and this script reaches its "rollback container
# also unhealthy" branch, turning one dead container into two.  Until R-F8
# lands: TAKE A pg_dump BEFORE DEPLOYING A RELEASE WITH MIGRATIONS.
#
# Shekel's prod image is pinned in /opt/docker/shekel/.env via
# SHEKEL_IMAGE_DIGEST (the compose override consumes it as
# `ghcr.io/saltyreformed/shekel@${SHEKEL_IMAGE_DIGEST:?...}`).
# bump-digest.sh can't touch this because it rejects env-var-templated
# images on purpose -- this script is the canonical path for shekel.
#
# Behavior:
#   1. Resolve target digest:
#        - default: pull `ghcr.io/saltyreformed/shekel:latest`, take its digest
#        - explicit: argv[1] of the form `sha256:<hex>` (skip the pull)
#   2. If target == current SHEKEL_IMAGE_DIGEST: exit 0, no-op.
#   3. cosign verify the target against the keyless OIDC identity used
#      by .github/workflows/docker-publish.yml. Aborts if cosign is
#      missing -- install it or pass --no-verify (NOT recommended).
#   4. Snapshot the old digest, rewrite SHEKEL_IMAGE_DIGEST in .env,
#      `docker compose up -d app`.
#   5. Poll the container's healthcheck for up to 4 minutes.
#   6. On healthy: ntfy success ping.
#      On unhealthy or compose failure: revert .env, recreate with the
#      old digest, ntfy failure ping with the digest pair, exit 1.
#
# Usage:
#   shekel-deploy.sh                   # pull :latest and deploy if new
#   shekel-deploy.sh sha256:abc...     # deploy that specific digest
#   shekel-deploy.sh --no-verify       # skip cosign (emergencies only)
#   shekel-deploy.sh --dry-run         # show plan, do not change anything
#   shekel-deploy.sh -h | --help

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────
SHEKEL_DIR="/opt/docker/shekel"
ENV_FILE="${SHEKEL_DIR}/.env"
COMPOSE_SERVICE="app"
CONTAINER_NAME="shekel-prod-app"
IMAGE_REPO="ghcr.io/saltyreformed/shekel"
MOVING_TAG="latest"

# cosign keyless verification -- must match the workflow that signed the image.
COSIGN_IDENTITY_REGEXP="https://github.com/SaltyReformed/Shekel/.github/workflows/docker-publish.yml@.*"
COSIGN_OIDC_ISSUER="https://token.actions.githubusercontent.com"

# ntfy
NTFY_URL="https://ntfy.saltyreformed.com"
NTFY_TOPIC="alerts"
NTFY_TOKEN_FILE="/opt/docker/monitoring/secrets/wud_ntfy_token"

# Health poll
HEALTH_TIMEOUT_S=240
HEALTH_INTERVAL_S=5

# ── Arg parsing ────────────────────────────────────────────────────
usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

verify=true
dryrun=false
explicit_digest=""
for a in "$@"; do
    case "$a" in
        -h | --help) usage 0 ;;
        --no-verify) verify=false ;;
        --dry-run) dryrun=true ;;
        sha256:*)
            if [ -n "$explicit_digest" ]; then
                echo "ERROR: more than one digest argument given." >&2
                exit 2
            fi
            explicit_digest="$a"
            ;;
        *)
            echo "ERROR: unknown argument: $a" >&2
            usage 2
            ;;
    esac
done

# ── Helpers ────────────────────────────────────────────────────────
log() { printf '[shekel-deploy] %s\n' "$*"; }
die() {
    printf '[shekel-deploy] ERROR: %s\n' "$*" >&2
    exit 1
}

ntfy_notify() {
    # ntfy_notify <priority 1-5> <title> <body>
    local prio="$1" title="$2" body="$3"
    if [ ! -r "$NTFY_TOKEN_FILE" ]; then
        log "WARN: $NTFY_TOKEN_FILE unreadable; skipping ntfy."
        return 0
    fi
    local token
    token=$(tr -d '\r\n' <"$NTFY_TOKEN_FILE")
    curl -fsS --max-time 10 \
        -H "Authorization: Bearer ${token}" \
        -H "Title: ${title}" \
        -H "Priority: ${prio}" \
        -H "Tags: shekel,deploy" \
        -d "${body}" \
        "${NTFY_URL}/${NTFY_TOPIC}" >/dev/null \
        || log "WARN: ntfy publish failed (non-fatal)."
}

short() { printf '%s' "${1#sha256:}" | cut -c1-12; }

read_env_digest() {
    awk -F= '/^SHEKEL_IMAGE_DIGEST=/{sub(/^SHEKEL_IMAGE_DIGEST=/,""); print; exit}' "$ENV_FILE"
}

write_env_digest() {
    # In-place rewrite preserving file perms (.env is mode 600 josh:josh).
    local new="$1"
    local tmp
    tmp=$(mktemp "${ENV_FILE}.XXXXXX")
    trap 'rm -f "$tmp"' RETURN
    # POSIX sed with a quoted | delimiter is safe -- sha256:... has no |.
    sed "s|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=${new}|" "$ENV_FILE" >"$tmp"
    if ! grep -q "^SHEKEL_IMAGE_DIGEST=${new}$" "$tmp"; then
        rm -f "$tmp"
        die "failed to rewrite SHEKEL_IMAGE_DIGEST in $ENV_FILE (line missing?)"
    fi
    chmod --reference="$ENV_FILE" "$tmp"
    mv "$tmp" "$ENV_FILE"
    trap - RETURN
}

wait_healthy() {
    local elapsed=0 status
    while [ "$elapsed" -lt "$HEALTH_TIMEOUT_S" ]; do
        status=$(docker inspect "$CONTAINER_NAME" \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            2>/dev/null || echo missing)
        case "$status" in
            healthy)
                log "container healthy after ${elapsed}s."
                return 0
                ;;
            unhealthy)
                log "container reported unhealthy after ${elapsed}s."
                return 1
                ;;
            starting) : ;;
            *) log "health status: $status" ;;
        esac
        sleep "$HEALTH_INTERVAL_S"
        elapsed=$((elapsed + HEALTH_INTERVAL_S))
    done
    log "timed out waiting for healthy after ${HEALTH_TIMEOUT_S}s."
    return 1
}

compose_up() {
    (cd "$SHEKEL_DIR" && docker compose up -d "$COMPOSE_SERVICE")
}

# ── Pre-flight ─────────────────────────────────────────────────────
# Captured once rather than substituted inside each message: a command
# substitution inside a string masks the inner command's status (SC2312), and
# the value is the same for the whole run.
whoami_name=$(id -un)
[ -r "$ENV_FILE" ] || die "$ENV_FILE not readable."
[ -w "$ENV_FILE" ] || die "$ENV_FILE not writable by ${whoami_name}."
docker info >/dev/null 2>&1 || die "docker daemon unreachable for ${whoami_name}."

old_digest=$(read_env_digest)
[ -n "$old_digest" ] || die "no SHEKEL_IMAGE_DIGEST in $ENV_FILE."
case "$old_digest" in
    sha256:*) : ;;
    *) die "current SHEKEL_IMAGE_DIGEST is not sha256:... (got '$old_digest')." ;;
esac

# ── Resolve target digest ──────────────────────────────────────────
if [ -n "$explicit_digest" ]; then
    new_digest="$explicit_digest"
    log "explicit target: $new_digest"
    if ! docker pull -q "${IMAGE_REPO}@${new_digest}" >/dev/null; then
        die "docker pull ${IMAGE_REPO}@${new_digest} failed."
    fi
else
    log "pulling ${IMAGE_REPO}:${MOVING_TAG} ..."
    if ! docker pull -q "${IMAGE_REPO}:${MOVING_TAG}" >/dev/null; then
        die "docker pull ${IMAGE_REPO}:${MOVING_TAG} failed."
    fi
    # Resolve the just-pulled tag's index/manifest digest.
    new_digest=$(docker image inspect "${IMAGE_REPO}:${MOVING_TAG}" \
        --format '{{range .RepoDigests}}{{println .}}{{end}}' \
        | grep -oE 'sha256:[a-f0-9]{64}' | head -1)
    [ -n "$new_digest" ] || die "could not resolve digest for ${IMAGE_REPO}:${MOVING_TAG}."
fi

# Short forms captured once, for the same reason as ``whoami_name`` above.
old_short=$(short "$old_digest")
new_short=$(short "$new_digest")

log "current pin: $old_digest"
log "target pin:  $new_digest"

if [ "$old_digest" = "$new_digest" ]; then
    log "already at target digest; nothing to do."
    exit 0
fi

# ── cosign verify ──────────────────────────────────────────────────
if $verify; then
    if ! command -v cosign >/dev/null 2>&1; then
        cat >&2 <<EOF
[shekel-deploy] ERROR: cosign is not installed.
  Your prod image is keyless-signed by .github/workflows/docker-publish.yml
  and your audit posture (F-155) requires verification before deploy.

  Install (one-shot, current stable):
    curl -sSL -o /tmp/cosign \\
      https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64
    sudo install -m 0755 /tmp/cosign /usr/local/bin/cosign
    cosign version

  Then re-run this script.
  Override (NOT recommended -- bypasses the supply-chain check):
    $0 --no-verify $*
EOF
        exit 1
    fi
    log "cosign verify ${IMAGE_REPO}@${new_digest} ..."
    if ! cosign verify \
        --certificate-identity-regexp "$COSIGN_IDENTITY_REGEXP" \
        --certificate-oidc-issuer "$COSIGN_OIDC_ISSUER" \
        "${IMAGE_REPO}@${new_digest}" >/dev/null 2>&1; then
        die "cosign verify FAILED for ${IMAGE_REPO}@${new_digest}. Aborting."
    fi
    log "cosign verify OK."
else
    log "WARN: cosign verify skipped (--no-verify)."
fi

# ── Dry-run exit ───────────────────────────────────────────────────
if $dryrun; then
    log "dry-run: would rewrite .env and recreate $CONTAINER_NAME."
    log "         $old_digest"
    log "      -> $new_digest"
    exit 0
fi

# ── Deploy ─────────────────────────────────────────────────────────
log "rewriting SHEKEL_IMAGE_DIGEST in $ENV_FILE ..."
write_env_digest "$new_digest"

log "docker compose up -d $COMPOSE_SERVICE ..."
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if ! compose_up; then
    log "compose up failed; reverting .env to $old_digest."
    write_env_digest "$old_digest"
    # shellcheck disable=SC2310 ## deliberate: the WARN is the handling.
    compose_up || log "WARN: revert compose up also failed; container state unknown."
    ntfy_notify 5 "Shekel deploy FAILED (compose)" \
        "compose up rejected ${new_short}; reverted to ${old_short}. Check: docker logs $CONTAINER_NAME"
    exit 1
fi

log "waiting for $CONTAINER_NAME to report healthy (timeout ${HEALTH_TIMEOUT_S}s) ..."
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if wait_healthy; then
    log "deploy OK: ${old_short} -> ${new_short}"
    ntfy_notify 3 "Shekel deployed" \
        "${old_short} -> ${new_short}"
    exit 0
fi

# ── Rollback ───────────────────────────────────────────────────────
log "rolling back to $old_digest ..."
write_env_digest "$old_digest"
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if compose_up && wait_healthy; then
    log "rollback healthy. Failed digest: $new_digest"
    ntfy_notify 5 "Shekel deploy FAILED, rolled back" \
        "${new_short} did not become healthy. Reverted to ${old_short}. Investigate: docker logs $CONTAINER_NAME"
    exit 1
fi

# Rollback also unhealthy -- manual intervention required.
ntfy_notify 5 "Shekel deploy FAILED, rollback UNHEALTHY" \
    "Neither ${new_short} nor rollback ${old_short} is healthy. Container needs manual attention."
die "rollback container also unhealthy; manual intervention required."
