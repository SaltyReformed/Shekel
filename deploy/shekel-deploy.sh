#!/usr/bin/env bash
# shekel-deploy.sh -- pull, verify, and roll shekel-prod-app to a new image.
#
# Usage:
#   shekel-deploy.sh                   # pull :latest and deploy if new
#   shekel-deploy.sh sha256:abc...     # deploy that specific digest
#   shekel-deploy.sh --no-verify       # skip cosign (emergencies only)
#   shekel-deploy.sh --dry-run         # show the plan, change nothing
#   shekel-deploy.sh -h | --help
#
# Every run dumps the database before it touches the pin, and refuses to
# re-pin when the previous image cannot resolve the schema revision the
# database has been left at.  The notes below say why.

# ── Which copy of this file is actually running ────────────────────
# The canonical copy is `deploy/shekel-deploy.sh` in the Shekel repository.
# The INTENDED install (developer ruling 2026-08-08) makes
# /opt/docker/scripts/shekel-deploy.sh -- which /usr/local/bin/shekel-deploy
# symlinks -- a SYMLINK to that copy, so the two cannot drift:
#
#   sudo ln -sfn /home/josh/projects/Shekel/deploy/shekel-deploy.sh \
#                /opt/docker/scripts/shekel-deploy.sh
#
# Until that link exists the host carries a HAND-COPY, and a hand-copy is how
# finding F-14 happened: the one path that rolls production was untracked and
# unreviewed, which is why F-8 below survived unnoticed.  This script does not
# assert which arrangement is in place -- it MEASURES it at startup
# (`report_installed_copy`) and says so in its own output, because a comment
# claiming "these are the same file" is exactly the claim that goes stale.
#
# Accepted cost of the symlink, ruled: the CHECKED-OUT WORKING TREE becomes
# the live deploy path.  A half-finished edit or a feature branch is live the
# moment it is saved.  Check out the revision you mean to deploy from.
#
# ── F-8, and what this script does about it ────────────────────────
# Plan step R-F8, ruling R-R14.  Migrations run at entrypoint step 3, BEFORE
# the health check, so a failed deploy can leave the database stamped at a
# revision the other image's Alembic tree does not contain.  That image then
# dies at step 3 too, and re-pinning it turns one dead container into two --
# reproduced on revision d4a71f6e30bb as `CommandError: Can't locate revision`.
#
# THE QUESTION IS NOT "does this release add migrations".  That is directional,
# and it gets the rollback case backwards: deploying an OLDER image adds
# nothing while being exactly the image that cannot resolve today's schema.
# The question is "can image X resolve the revision the database is stamped
# at", asked of the real `alembic_version` row.  So:
#
#   * before anything is written, the TARGET must be able to resolve the
#     revision the database is stamped at now -- otherwise it would die at
#     step 3 and the deploy is refused up front;
#   * every deploy takes a pg_dump -Fc first; no dump, no deploy;
#   * after a failure the stamp is RE-READ, and the pin is reverted only if
#     the previous image can resolve what the database now says.  Otherwise
#     the script names the dump and stops.  A release that migrated nothing
#     leaves the stamp untouched, so it still rolls back automatically -- 6 of
#     the last 10 releases were pure digest reverts.
#
# Shekel's prod image is pinned in /opt/docker/shekel/.env via
# SHEKEL_IMAGE_DIGEST (the compose override consumes it as
# `ghcr.io/saltyreformed/shekel@${SHEKEL_IMAGE_DIGEST:?...}`).
# bump-digest.sh can't touch this because it rejects env-var-templated
# images on purpose -- this script is the canonical path for shekel.

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

report_installed_copy() {
    # Say which file is executing and whether it is the repository's copy.
    # MEASURED rather than asserted: this header previously claimed the host
    # path was a symlink to the repo, and a stale hand-copy made that claim
    # false while reading as reassurance.  Printing the answer on every run is
    # what keeps it honest.
    local running canonical canonical_real
    running=$(readlink -f "$0")
    log "running: ${running}"
    canonical="${SHEKEL_CANONICAL_COPY:-/home/josh/projects/Shekel/deploy/shekel-deploy.sh}"
    [ -r "$canonical" ] || return 0
    canonical_real=$(readlink -f "$canonical")
    if [ "$running" = "$canonical_real" ]; then
        return 0
    fi
    log "WARN: this is NOT the repository's copy (${canonical})."
    if diff -q "$running" "$canonical" >/dev/null 2>&1; then
        log "      Contents match today, but nothing keeps them in step."
    else
        log "      Contents DIFFER -- the reviewed script is not the one about"
        log "      to roll production.  Compare them before continuing:"
        log "        diff '${running}' '${canonical}'"
    fi
    log "      Install the symlink: sudo ln -sfn '${canonical}' \\"
    log "        /opt/docker/scripts/shekel-deploy.sh"
}

image_migration_revisions() {
    # Echo the migration filenames an image carries, one per line, C-sorted.
    #
    # Runs the image with its entrypoint replaced, so nothing boots: no
    # database connection, no migration, no server.  The listing is NOT
    # error-suppressed: a missing directory -- a `migrations/` rename, a
    # changed WORKDIR -- must surface as a failure, because an empty list is
    # otherwise indistinguishable from "this image has no migrations" and
    # would silently disable every check built on it.
    local digest="$1"
    docker run --rm --entrypoint /bin/sh "${IMAGE_REPO}@${digest}" -c \
        "ls -1 ${IMAGE_MIGRATIONS_DIR}/*.py | xargs -r -n1 basename | LC_ALL=C sort"
}

image_listing_or_die() {
    # Echo one image's migration listing, refusing an empty or failed answer.
    local digest="$1" role="$2" listing
    # shellcheck disable=SC2310 ## deliberate, and the shape used throughout
    # this script: the helper is one `docker run` whose status IS the branch
    # condition, so errexit being disabled inside it masks nothing.
    if ! listing=$(image_migration_revisions "$digest"); then
        die "could not list ${IMAGE_MIGRATIONS_DIR} in the ${role} image
   (${digest}).  Refusing to deploy: every check below rests on that listing,
   and guessing would re-arm the failure this script exists to stop."
    fi
    if [ -z "$listing" ]; then
        die "the ${role} image (${digest}) reports ZERO migrations in
   ${IMAGE_MIGRATIONS_DIR}.  Every real image carries over a hundred, so this
   is a broken probe -- a renamed directory or a changed WORKDIR -- not an
   image without migrations.  Refusing to read it as 'nothing to worry about',
   which is how this check would silently stop working."
    fi
    printf '%s\n' "$listing"
}

db_stamped_revisions() {
    # Echo the Alembic revision(s) the database is currently stamped at.
    # This row is the authority: `CommandError: Can't locate revision` names
    # this value, so "can image X boot against this database" is decidable
    # from it plus X's migration listing.
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
        "SELECT version_num FROM public.alembic_version"
}

image_resolves_revisions() {
    # 0 when *listing* carries a migration script for every revision given.
    # Alembic resolves a revision by finding the script whose id it is, so
    # file presence answers the same question without booting anything.
    local listing="$1" revisions="$2" rev
    for rev in $revisions; do
        printf '%s\n' "$listing" | grep -q "^${rev}_" || return 1
    done
    return 0
}

preflight_migrations() {
    # Establish OLD_MIGRATIONS, NEW_MIGRATIONS, STAMPED_REVISIONS and the
    # informational ADDED_REVISIONS, then refuse a target that could not boot
    # against the database as it stands.
    #
    # "Does this release ADD migrations" is NOT the safety question -- it is
    # directional, and it reads a rollback (an older target, which adds
    # nothing) as safe when it is precisely the image that cannot resolve
    # today's schema.  The question asked here is direction-free.
    local old="$1" new="$2"
    log "pre-flight: reading ${IMAGE_MIGRATIONS_DIR} from both images ..."
    OLD_MIGRATIONS=$(image_listing_or_die "$old" "CURRENT")
    NEW_MIGRATIONS=$(image_listing_or_die "$new" "TARGET")

    # LC_ALL=C on comm: the listings were sorted in the container's C locale,
    # and comm verifies its inputs' order in the CALLER's.  Today's filenames
    # collate identically either way, but not every revision id here is hex
    # (c3d4e5f6g7h8_... exists), so that agreement is not structural.
    ADDED_REVISIONS=$(LC_ALL=C comm -13 \
        <(printf '%s\n' "$OLD_MIGRATIONS") \
        <(printf '%s\n' "$NEW_MIGRATIONS"))

    # shellcheck disable=SC2310 ## boolean predicate; see image_listing_or_die.
    if ! STAMPED_REVISIONS=$(db_stamped_revisions); then
        die "could not read public.alembic_version from ${DB_CONTAINER}.  That
   row decides whether a rollback is possible, so this script will not proceed
   without it."
    fi
    if [ -z "$STAMPED_REVISIONS" ]; then
        die "public.alembic_version is EMPTY on ${DB_NAME}.  An uninitialised
   database is not a state this script should deploy over."
    fi
    local stamped_line
    stamped_line=$(printf '%s' "$STAMPED_REVISIONS" | tr '\n' ' ')
    log "database is stamped at: ${stamped_line}"

    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! image_resolves_revisions "$NEW_MIGRATIONS" "$STAMPED_REVISIONS"; then
        die "the TARGET image cannot resolve the revision this database is
   stamped at, so it would die at entrypoint step 3 exactly as the previous
   image does in finding F-8.  This is what deploying an image OLDER than the
   database looks like.  A genuine downgrade means restoring a dump taken at
   or before that revision FIRST, then deploying this digest."
    fi
}

repin_is_safe() {
    # 0 when the previously-pinned image can resolve whatever revision the
    # database is stamped at NOW.  Asked AFTER a failure rather than before:
    # the deploy may have migrated before falling over, and this is the only
    # question whose answer decides whether re-pinning recovers or kills.
    # It also distinguishes the two compose-failure cases for free -- a
    # container that never started migrated nothing, so the stamp is unchanged
    # and the ordinary rollback still applies.
    local stamped
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! stamped=$(db_stamped_revisions) || [ -z "$stamped" ]; then
        log "could not re-read public.alembic_version after the failure;"
        log "treating the rollback as unsafe."
        return 1
    fi
    local stamped_line
    stamped_line=$(printf '%s' "$stamped" | tr '\n' ' ')
    log "database is now stamped at: ${stamped_line}"
    STAMPED_REVISIONS="$stamped"
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    image_resolves_revisions "$OLD_MIGRATIONS" "$stamped"
}

take_predeploy_dump() {
    # pg_dump -Fc the whole database, then prove the artifact is readable.
    # Sets DUMP_PATH.  Any failure is fatal and happens BEFORE the pin moves.
    #
    # Written to a .part file and renamed only after the archive DECODES: a
    # truncated dump that wears a valid name is worse than no dump, because it
    # is the thing the failure path tells the operator to restore from (the
    # partial-file discipline audit finding OPS/SH-04 established for the
    # backup family).  DUMP_PART is global so the EXIT trap sweeps a straggler
    # left by a Ctrl-C, which is OPS/SH-04's third part.
    #
    # The readback is `pg_restore -f /dev/null`, NOT `pg_restore -l`.  Measured
    # 2026-08-08 on a real 1.1 MB archive: -l reads only the table of contents,
    # which lives in the first ~8 KB, so archives truncated at 50% and even 99%
    # PASS it.  Restoring one of those after `--clean` empties the database and
    # then stops.  -f decodes every data block, which is the -Fc analogue of
    # the gzip integrity decode `_backup_lib.sh:validate_backup_artifact` does
    # for the nightly backups.
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    DUMP_PATH="${BACKUP_DIR}/shekel_prod_predeploy_${new_short}_${ts}.dump"
    DUMP_PART="${DUMP_PATH}.part"

    mkdir -p "$BACKUP_DIR" || die "cannot create backup directory $BACKUP_DIR."
    if ! docker inspect --format='{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null | grep -q true; then
        die "database container '$DB_CONTAINER' is not running; no dump, no deploy."
    fi

    log "dumping ${DB_NAME} to ${DUMP_PATH} ..."
    # stderr is CAPTURED, not discarded: "pg_dump failed" without the reason
    # sends the operator hunting for a disk-full, an auth failure or a lock
    # timeout that the message already had in hand.
    local errfile status=0
    errfile=$(mktemp "${TMPDIR:-/tmp}/shekel-deploy-pgdump.XXXXXX")
    docker exec "$DB_CONTAINER" \
        pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc >"$DUMP_PART" 2>"$errfile" || status=$?
    if [ "$status" -ne 0 ]; then
        local reason
        reason=$(tr -d '\r' <"$errfile" | tail -3)
        rm -f "$errfile" "$DUMP_PART"
        DUMP_PART=""
        die "pg_dump exited ${status}; no dump, no deploy.  The pin has NOT
   been touched.  pg_dump said:
${reason}"
    fi
    rm -f "$errfile"

    if ! docker exec -i "$DB_CONTAINER" \
        pg_restore -f /dev/null <"$DUMP_PART" >/dev/null 2>&1; then
        rm -f "$DUMP_PART"
        DUMP_PART=""
        die "the dump was written but does not decode end to end -- treating
   it as truncated or corrupt.  No dump, no deploy."
    fi

    mv "$DUMP_PART" "$DUMP_PATH"
    DUMP_PART=""
    local bytes
    bytes=$(stat -c %s "$DUMP_PATH")
    log "dump OK: $DUMP_PATH (${bytes} bytes, decoded end to end)"
}

refuse_to_repin() {
    # The F-8 failure path: the previously-pinned image cannot resolve the
    # revision the database is stamped at NOW, so re-pinning it produces a
    # SECOND dead container rather than a recovery.  Name the dump and stop.
    #
    # The first instruction is to STOP the container, and that ordering is not
    # cosmetic: the app runs `restart: unless-stopped` and re-runs
    # `alembic upgrade head` on every start, so an operator who begins with
    # pg_restore is restoring underneath a crash-looping container that is
    # concurrently migrating.
    local why="$1"
    log ""
    log "REFUSING to roll back."
    log "  ${why}"
    log ""
    log "  ${old_short} does not contain a migration script for the revision"
    log "  the database is stamped at, so it would die at entrypoint step 3"
    log "  exactly as ${new_short} just did.  The pin is left at ${new_short}"
    log "  deliberately; change it only after the database is restored."
    log ""
    local stamped_line
    stamped_line=$(printf '%s' "$STAMPED_REVISIONS" | tr '\n' ' ')
    log "  Database is stamped at: ${stamped_line}"
    if [ -n "$ADDED_REVISIONS" ]; then
        log "  Migrations this release added:"
        printf '%s\n' "$ADDED_REVISIONS" | sed 's/^/    /'
    fi
    log ""
    log "  Pre-deploy dump:"
    log "    ${DUMP_PATH}"
    log ""
    log "  DATA LOSS WARNING: restoring that dump discards everything written"
    log "  since it was taken, a few minutes ago.  In a budgeting app that is"
    log "  real entries.  Dump the CURRENT state first so the window is"
    log "  recoverable:"
    log "    docker exec ${DB_CONTAINER} pg_dump -U ${DB_USER} -d ${DB_NAME} \\"
    log "        -Fc >'${DUMP_PATH}.failed-state'"
    log ""
    log "  Then, to restore and return to ${old_short}:"
    log "    cd ${SHEKEL_DIR}"
    log "    docker compose stop ${COMPOSE_SERVICE}   # FIRST: it re-migrates on every restart"
    log "    docker exec -i ${DB_CONTAINER} pg_restore -U ${DB_USER} \\"
    log "        -d ${DB_NAME} --clean --if-exists \\"
    log "        --single-transaction --exit-on-error <'${DUMP_PATH}'"
    log "    sed -i 's|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=${old_digest}|' ${ENV_FILE}"
    log "    docker compose up -d ${COMPOSE_SERVICE}"
    log ""
    log "  --single-transaction --exit-on-error matter: without them a stream"
    log "  that ends early leaves the database EMPTY after --clean has already"
    log "  dropped everything, with no transaction to roll back (OPS/SH-01)."
    log "  --clean also drops only what the archive knows about, so a table the"
    log "  failed migration created survives and will collide on the retry:"
    log "    docker exec ${DB_CONTAINER} psql -U ${DB_USER} -d ${DB_NAME} \\"
    log "        -c '\\dt budget.*'"
    log ""
    log "  Read the container's logs first -- a failure that never reached the"
    log "  migration step needs no restore at all, only the pin:"
    log "    docker logs ${CONTAINER_NAME}"

    ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED" \
        "${new_short} did not come up and ${old_short} cannot resolve the schema the database is now at, so re-pinning cannot work. Pin left at ${new_short}. FIRST run: docker compose stop ${COMPOSE_SERVICE} (it re-migrates on every restart). Dump: ${DUMP_PATH}"
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
# Globals the helpers above set and read.  Declared here so `set -u` cannot be
# tripped by a path that reaches a failure branch before the pre-flight ran.
OLD_MIGRATIONS=""
NEW_MIGRATIONS=""
ADDED_REVISIONS=""
STAMPED_REVISIONS=""
DUMP_PATH=""
DUMP_PART=""

# shellcheck disable=SC2329 ## invoked indirectly, by the `trap ... EXIT`
# immediately below; shellcheck does not follow trap handlers.
sweep_part_file() {
    # OPS/SH-04's third part: a Ctrl-C mid-dump must not leave a straggler.
    # if-form, not `[ ] &&` -- errexit is ACTIVE inside an EXIT trap, so a
    # falsy last command would override the script's real exit status.
    if [ -n "$DUMP_PART" ]; then
        rm -f "$DUMP_PART"
    fi
    return 0
}
trap sweep_part_file EXIT

report_installed_copy

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
    # "Nothing to do" is a claim about the PIN.  After a refused rollback the
    # pin deliberately equals the target while the app is down, and re-running
    # this script is the operator's most natural next move -- so exiting 0
    # here without looking at the container reports success over a dead stack.
    health=$(docker inspect "$CONTAINER_NAME" \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        2>/dev/null || echo missing)
    if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
        log "already at target digest; nothing to do."
        exit 0
    fi
    log "already at target digest, but $CONTAINER_NAME is '${health}'."
    log "  The pin is not the problem, so re-running a deploy will not fix it."
    log "  If a previous run REFUSED a rollback, the database still needs"
    log "  restoring -- that run named the dump and the exact sequence."
    log "    docker logs ${CONTAINER_NAME}"
    ntfy_notify 4 "Shekel deploy no-op, container ${health}" \
        "Pin already at ${new_short} but ${CONTAINER_NAME} is ${health}. Deploying cannot help; check whether a refused rollback is still outstanding."
    exit 1
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
# Before anything is written.  Refuses a target that could not boot against
# this database, and says whether a failure would be recoverable by
# re-pinning -- the dry-run reports it, so that is answerable without
# deploying.
preflight_migrations "$old_digest" "$new_digest"

if [ -n "$ADDED_REVISIONS" ]; then
    log "MIGRATION-BEARING release: the target adds these revisions --"
    printf '%s\n' "$ADDED_REVISIONS" | sed 's/^/    /'
    log "  If it runs them and then fails the health check, re-pinning"
    log "  ${old_short} will NOT work -- migrations run at entrypoint step 3,"
    log "  before the health check.  This script re-reads the stamp after a"
    log "  failure and refuses rather than making a second dead container."
else
    log "the target adds no migrations, so a failure leaves the schema where"
    log "${old_short} can still resolve it and the rollback applies as usual."
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
take_predeploy_dump

# ── Deploy ─────────────────────────────────────────────────────────
log "rewriting SHEKEL_IMAGE_DIGEST in $ENV_FILE ..."
write_env_digest "$new_digest"

log "docker compose up -d $COMPOSE_SERVICE ..."
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if ! compose_up; then
    # `docker compose up -d` reporting failure USUALLY means the container
    # never started, so nothing migrated -- but it also covers one that
    # started and exited having reached entrypoint step 3.  Rather than guess
    # from the exit status, ask the database: if the stamp is still something
    # ${old_short} can resolve, the ordinary revert is correct.
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! repin_is_safe; then
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
# shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
if ! repin_is_safe; then
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

# Rollback also unhealthy -- manual intervention required.  Reached only when
# the previous image CAN resolve the current stamp, so the schema is not the
# suspect; the dump is a starting point rather than the recovery.
log "pre-deploy dump kept at: ${DUMP_PATH}"
ntfy_notify 5 "Shekel deploy FAILED, rollback UNHEALTHY" \
    "Neither ${new_short} nor rollback ${old_short} is healthy. Container needs manual attention. Dump: ${DUMP_PATH}"
die "rollback container also unhealthy; manual intervention required."
