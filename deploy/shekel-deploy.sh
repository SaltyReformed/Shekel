#!/usr/bin/env bash
# shekel-deploy.sh -- pull, verify, and roll shekel-prod-app to a new image.
#
# THIS FILE IS THE CANONICAL COPY, and since 2026-08-08 it is the ONLY one:
# /opt/docker/scripts/shekel-deploy.sh is a SYMLINK to it, and
# /usr/local/bin/shekel-deploy symlinks that.  It lived only on the host until
# 2026-08-08 (plan finding F-14): the one path that rolls production was
# untracked, unreviewed, and outside every linter in the polyglot gate, which
# is why finding F-8 -- a rollback that cannot work for a migration-bearing
# release -- survived unnoticed.
#
# CONSEQUENCE OF THE SYMLINK, ruled and accepted 2026-08-08: the CHECKED-OUT
# WORKING TREE is what rolls production.  A half-finished edit, a feature
# branch or a stash is live the moment it is saved.  Check out the revision
# you mean to deploy from before running this.  Install with:
#   sudo ln -sfn /home/josh/projects/Shekel/deploy/shekel-deploy.sh \
#                /opt/docker/scripts/shekel-deploy.sh
#
# F-8, and what this script now does about it (plan step R-F8, ruling R-R14).
# Migrations run at entrypoint step 3, BEFORE the health check, so a failed
# migration-bearing deploy leaves the database stamped at a revision the
# previously-pinned image cannot resolve; that image then dies at step 3 too,
# and re-pinning to it turns one dead container into two.  A rollback is
# therefore IMPOSSIBLE for such a release, not merely unreliable -- reproduced
# on revision d4a71f6e30bb as `CommandError: Can't locate revision`.  So:
#
#   * every deploy takes a pg_dump -Fc FIRST; no dump, no deploy;
#   * a pre-flight compares the two images' migration sets and SAYS when the
#     release is migration-bearing;
#   * for such a release, a failure does NOT re-pin.  It names the dump and
#     the restore command and stops, because re-pinning is the thing that
#     cannot work.  For every other release the automatic rollback is intact
#     and unchanged -- 6 of the last 10 were pure digest reverts.
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
#   4. Migration pre-flight: list migrations/versions in BOTH images and
#      report the revisions the target adds. Non-empty => MIGRATION-BEARING,
#      which changes step 7.
#   5. pg_dump -Fc of the whole database to $SHEKEL_BACKUP_DIR, written to a
#      .part file and renamed only after pg_restore -l reads it back. A
#      failure here aborts BEFORE the pin is touched.
#   6. Snapshot the old digest, rewrite SHEKEL_IMAGE_DIGEST in .env,
#      `docker compose up -d app`; poll the healthcheck for up to 4 minutes.
#   7. On healthy: ntfy success ping.
#      On failure, NOT migration-bearing: revert .env, recreate with the old
#      digest, ntfy the digest pair, exit 1 (unchanged behaviour).
#      On failure, MIGRATION-BEARING: do NOT re-pin. Print and ntfy the dump
#      path and the restore sequence, exit 1.
#
# Usage:
#   shekel-deploy.sh                   # pull :latest and deploy if new
#   shekel-deploy.sh sha256:abc...     # deploy that specific digest
#   shekel-deploy.sh --no-verify       # skip cosign (emergencies only)
#   shekel-deploy.sh --dry-run         # show plan, do not change anything
#   shekel-deploy.sh -h | --help

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────
# Every value is overridable from the environment, defaulting to production.
# That is not configurability for its own sake: R-F8's negative control has to
# drive this script end to end against a THROWAWAY stack and a restored clone
# -- planting a failing health check on a migration-bearing deploy and
# watching it refuse -- and a script whose targets are literals can only be
# tested by reading it.  `scripts/deploy.sh` states the same convention.
SHEKEL_DIR="${SHEKEL_DIR:-/opt/docker/shekel}"
ENV_FILE="${SHEKEL_ENV_FILE:-${SHEKEL_DIR}/.env}"
COMPOSE_SERVICE="${SHEKEL_COMPOSE_SERVICE:-app}"
CONTAINER_NAME="${SHEKEL_CONTAINER_NAME:-shekel-prod-app}"
IMAGE_REPO="${SHEKEL_IMAGE_REPO:-ghcr.io/saltyreformed/shekel}"
MOVING_TAG="${SHEKEL_MOVING_TAG:-latest}"

# Pre-deploy dump target.  The directory and naming match the predeploy dumps
# the operator has been taking by hand before each migration-bearing release
# (~/shekel-backups/shekel_prod_predeploy_<label>_<ts>.dump); this automates
# that habit rather than inventing a second convention beside it.
DB_CONTAINER="${SHEKEL_DB_CONTAINER:-shekel-prod-db}"
DB_USER="${SHEKEL_DB_USER:-shekel_user}"
DB_NAME="${SHEKEL_DB_NAME:-shekel}"
BACKUP_DIR="${SHEKEL_BACKUP_DIR:-${HOME}/shekel-backups}"

# Path to the migrations directory INSIDE the image, used by the pre-flight.
IMAGE_MIGRATIONS_DIR="${SHEKEL_IMAGE_MIGRATIONS_DIR:-migrations/versions}"

# cosign keyless verification -- must match the workflow that signed the image.
COSIGN_IDENTITY_REGEXP="https://github.com/SaltyReformed/Shekel/.github/workflows/docker-publish.yml@.*"
COSIGN_OIDC_ISSUER="https://token.actions.githubusercontent.com"

# ntfy.  Overridable for the same reason as the block above -- a rehearsal
# that paged the operator with a fake "deploy FAILED" would be worse than no
# rehearsal.  Point NTFY_TOKEN_FILE at a nonexistent path to silence it.
NTFY_URL="${SHEKEL_NTFY_URL:-https://ntfy.saltyreformed.com}"
NTFY_TOPIC="${SHEKEL_NTFY_TOPIC:-alerts}"
NTFY_TOKEN_FILE="${SHEKEL_NTFY_TOKEN_FILE:-/opt/docker/monitoring/secrets/wud_ntfy_token}"

# Health poll
HEALTH_TIMEOUT_S="${SHEKEL_HEALTH_TIMEOUT_S:-240}"
HEALTH_INTERVAL_S="${SHEKEL_HEALTH_INTERVAL_S:-5}"

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

image_migration_revisions() {
    # Echo the migration filenames an image carries, one per line, sorted.
    # Listing files is the right question: the previous image can resolve the
    # database's stamped revision if and only if that revision's script is
    # present in it, which is exactly what Alembic looks for and exactly what
    # `CommandError: Can't locate revision` reports when it is not.
    #
    # Runs the image with its entrypoint replaced, so nothing boots: no
    # database connection, no migration, no server.  `|| true` inside the
    # container keeps an image with no migrations directory (not a shape that
    # exists today) reporting empty rather than aborting the deploy.
    # The sort happens INSIDE the container so both lists reach `comm` in one
    # collation, and so no host-side pipe masks a status (SC2312).
    local digest="$1"
    docker run --rm --entrypoint /bin/sh "${IMAGE_REPO}@${digest}" -c \
        "ls -1 ${IMAGE_MIGRATIONS_DIR}/*.py 2>/dev/null | xargs -r -n1 basename | LC_ALL=C sort || true"
}

preflight_migrations() {
    # Set MIGRATION_REVISIONS to the migrations the target image adds.
    # Empty means the release cannot move the schema, so the rollback below is
    # the ordinary, working one.
    local old="$1" new="$2"
    local old_list new_list
    log "migration pre-flight: comparing ${IMAGE_MIGRATIONS_DIR} in both images ..."
    # shellcheck disable=SC2310 ## deliberate, and the same shape the rest of
    # this script uses: the helper is one `docker run` whose status IS the
    # branch condition, so errexit being disabled inside it masks nothing.
    if ! old_list=$(image_migration_revisions "$old"); then
        die "could not list migrations in the CURRENT image ($old).  Refusing
   to deploy: without both lists this script cannot tell whether a failure
   would be recoverable by re-pinning, which is the whole safety property."
    fi
    # shellcheck disable=SC2310 ## as above.
    if ! new_list=$(image_migration_revisions "$new"); then
        die "could not list migrations in the TARGET image ($new).  See above."
    fi
    # Both lists are already sorted by the helper, so `comm`'s precondition
    # holds without a host-side sort.
    MIGRATION_REVISIONS=$(comm -13 \
        <(printf '%s\n' "$old_list") \
        <(printf '%s\n' "$new_list"))
}

take_predeploy_dump() {
    # pg_dump -Fc the whole database, then prove the artifact is readable.
    # Sets DUMP_PATH.  Any failure is fatal and happens BEFORE the pin moves.
    #
    # Written to a .part file and renamed only after `pg_restore -l` reads a
    # table of contents back out of it: a truncated dump that wears a valid
    # name is worse than no dump, because it is the thing the failure path
    # tells the operator to restore from (the partial-file discipline audit
    # finding OPS/SH-04 established for the backup family).
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    DUMP_PATH="${BACKUP_DIR}/shekel_prod_predeploy_${new_short}_${ts}.dump"
    local part="${DUMP_PATH}.part"

    mkdir -p "$BACKUP_DIR" || die "cannot create backup directory $BACKUP_DIR."
    if ! docker inspect --format='{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null | grep -q true; then
        die "database container '$DB_CONTAINER' is not running; no dump, no deploy."
    fi

    log "dumping ${DB_NAME} to ${DUMP_PATH} ..."
    if ! docker exec "$DB_CONTAINER" \
        pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc >"$part" 2>/dev/null; then
        rm -f "$part"
        die "pg_dump failed; no dump, no deploy.  The pin has NOT been touched."
    fi
    if ! docker exec -i "$DB_CONTAINER" pg_restore -l <"$part" >/dev/null 2>&1; then
        rm -f "$part"
        die "the dump was written but pg_restore could not read a table of
   contents from it -- treating it as truncated.  No dump, no deploy."
    fi
    mv "$part" "$DUMP_PATH"
    local bytes
    bytes=$(stat -c %s "$DUMP_PATH")
    log "dump OK: $DUMP_PATH (${bytes} bytes)"
}

refuse_to_repin() {
    # The F-8 failure path: a migration-bearing release that did not come up.
    # Re-pinning is what CANNOT work here -- the database is stamped at a
    # revision the previous image's Alembic tree does not contain, so that
    # image dies at entrypoint step 3 as well.  Name the dump and stop.
    local why="$1"
    log ""
    log "REFUSING to roll back: this release carries migrations."
    log "  ${why}"
    log ""
    log "  The database may already be stamped at a revision the previous"
    log "  image cannot resolve, so re-pinning it would produce a SECOND dead"
    log "  container rather than a recovery.  The pin is left at ${new_short}"
    log "  deliberately; change it only after the database is restored."
    log ""
    log "  Migrations this release added:"
    printf '%s\n' "$MIGRATION_REVISIONS" | sed 's/^/    /'
    log ""
    log "  Pre-deploy dump:"
    log "    ${DUMP_PATH}"
    log ""
    log "  To restore and return to ${old_short}:"
    log "    cd ${SHEKEL_DIR}"
    log "    docker compose stop ${COMPOSE_SERVICE}"
    log "    docker exec -i ${DB_CONTAINER} pg_restore -U ${DB_USER} \\"
    log "        -d ${DB_NAME} --clean --if-exists <'${DUMP_PATH}'"
    log "    sed -i 's|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=${old_digest}|' ${ENV_FILE}"
    log "    docker compose up -d ${COMPOSE_SERVICE}"
    log ""
    log "  Read the container's logs first -- a health failure that never"
    log "  reached the migration step needs no restore, only the pin:"
    log "    docker logs ${CONTAINER_NAME}"

    ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED (migrations)" \
        "${new_short} did not become healthy and the release carries migrations, so re-pinning to ${old_short} cannot work. Pin left at ${new_short}. Dump: ${DUMP_PATH}"
    exit 1
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

# ── Migration pre-flight ───────────────────────────────────────────
# Before anything is written.  Decides which failure path applies below, and
# the dry-run reports it, so "is this release recoverable by re-pinning?" is
# answerable without deploying.
MIGRATION_REVISIONS=""
preflight_migrations "$old_digest" "$new_digest"

if [ -n "$MIGRATION_REVISIONS" ]; then
    log "MIGRATION-BEARING release: the target adds these revisions --"
    printf '%s\n' "$MIGRATION_REVISIONS" | sed 's/^/    /'
    log "  Rollback for this release is BACKUP-ONLY.  Migrations run at"
    log "  entrypoint step 3, before the health check, so a failure can leave"
    log "  the database where ${old_short} cannot follow.  On failure this"
    log "  script will NOT re-pin; it will name the dump and stop."
else
    log "no new migrations in the target image; the automatic rollback to"
    log "${old_short} applies as usual."
fi

# ── Dry-run exit ───────────────────────────────────────────────────
if $dryrun; then
    log "dry-run: would dump the database, rewrite .env, and recreate $CONTAINER_NAME."
    log "         $old_digest"
    log "      -> $new_digest"
    exit 0
fi

# ── Pre-deploy dump ────────────────────────────────────────────────
# Unconditional, and BEFORE the pin is rewritten: no dump, no deploy.  It is
# taken for every release, not only migration-bearing ones, because "this
# release could not have moved the schema" is a claim about the image, and
# the dump costs about half a megabyte.
DUMP_PATH=""
take_predeploy_dump

# ── Deploy ─────────────────────────────────────────────────────────
log "rewriting SHEKEL_IMAGE_DIGEST in $ENV_FILE ..."
write_env_digest "$new_digest"

log "docker compose up -d $COMPOSE_SERVICE ..."
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if ! compose_up; then
    if [ -n "$MIGRATION_REVISIONS" ]; then
        # Fail closed.  `docker compose up -d` reporting failure USUALLY means
        # the container never started, so nothing migrated -- but it also
        # covers a container that started and exited, where the entrypoint DID
        # reach step 3.  The two are not distinguishable from the exit status,
        # and one of them makes re-pinning fatal, so neither re-pins.
        refuse_to_repin "docker compose up rejected ${new_short}."
    fi
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
    log "pre-deploy dump kept at: ${DUMP_PATH}"
    ntfy_notify 3 "Shekel deployed" \
        "${old_short} -> ${new_short}"
    exit 0
fi

# ── Rollback ───────────────────────────────────────────────────────
# The F-8 fork.  A migration-bearing release has no working rollback, so it
# gets the refusal instead of a second dead container.
if [ -n "$MIGRATION_REVISIONS" ]; then
    refuse_to_repin "${new_short} did not report healthy within ${HEALTH_TIMEOUT_S}s."
fi

log "rolling back to $old_digest ..."
write_env_digest "$old_digest"
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if compose_up && wait_healthy; then
    log "rollback healthy. Failed digest: $new_digest"
    log "pre-deploy dump kept at: ${DUMP_PATH}"
    ntfy_notify 5 "Shekel deploy FAILED, rolled back" \
        "${new_short} did not become healthy. Reverted to ${old_short}. Investigate: docker logs $CONTAINER_NAME"
    exit 1
fi

# Rollback also unhealthy -- manual intervention required.  Reached only for a
# release with NO new migrations, so the database is not the suspect and the
# dump is a starting point rather than the recovery.
log "pre-deploy dump kept at: ${DUMP_PATH}"
ntfy_notify 5 "Shekel deploy FAILED, rollback UNHEALTHY" \
    "Neither ${new_short} nor rollback ${old_short} is healthy. Container needs manual attention. Dump: ${DUMP_PATH}"
die "rollback container also unhealthy; manual intervention required."
