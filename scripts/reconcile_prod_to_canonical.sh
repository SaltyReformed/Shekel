#!/bin/bash
# Shekel Budget App -- One-shot production reconciliation.
#
# Reconciles /opt/docker/shekel/ to the repo's canonical state per
# /home/josh/.claude/plans/review-my-dev-docker-jiggly-quokka.md.
#
# Idempotent within reason -- skips steps whose output already exists.
# Run from the repo root.  After this completes the operator runs
# ``cd /opt/docker/shekel && docker compose down && docker compose up -d``
# to roll the stack.  This script intentionally STOPS short of that
# recreate so the operator has a chance to review the merged config
# (printed at the end of step 7) before pulling the trigger.
#
# Sudo is required ONLY for the cert chown (uid 70).  Everything else
# runs as the invoking user, relying on josh:josh ownership of
# /opt/docker/shekel/ and the docker group membership for ``docker``
# commands.
#
# Step-list:
#   1. Generate Postgres TLS cert (sudo for chown)
#   2. Create /opt/docker/shekel/{secrets,deploy/postgres} dirs
#   3. Migrate four high-sensitivity secrets from .env to files
#   4. Rewrite /opt/docker/shekel/.env (placeholders + new vars)
#   5. Create shekel-frontend network if missing
#   6. Update shared Nginx vhost (deploy/nginx-shared/conf.d/shekel.conf)
#   7. Copy repo compose files to /opt/docker/shekel/
#
# Exit codes:
#   0 -- all steps succeeded; operator may proceed with recreate.
#   1 -- a precondition failed; nothing was changed past the failure.
#   2 -- a destructive step refused because the input was unsafe.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROD_ROOT=/opt/docker/shekel
SHARED_NGINX_CONF_D=/opt/docker/nginx/conf.d

log() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31mFATAL: %s\033[0m\n' "$*" >&2; exit 1; }

# Sanity: run from repo root.
[[ -f "$REPO_ROOT/docker-compose.yml" ]] || die "must be invoked from the Shekel repo (cwd=$REPO_ROOT)"
[[ -f "$REPO_ROOT/deploy/docker-compose.prod.yml" ]] || die "deploy/docker-compose.prod.yml missing"
[[ -d "$PROD_ROOT" ]] || die "$PROD_ROOT does not exist; production not laid out as expected"

# ── 1. Generate Postgres TLS cert ─────────────────────────────────
log "Step 1: Postgres TLS cert"
mkdir -p "$PROD_ROOT/deploy/postgres"
if [[ -f "$PROD_ROOT/deploy/postgres/server.crt" && -f "$PROD_ROOT/deploy/postgres/server.key" ]]; then
    warn "cert + key already present at $PROD_ROOT/deploy/postgres/ -- skipping generation"
else
    # The script writes to ./deploy/postgres relative to the cwd it
    # is invoked from.  We chdir to PROD_ROOT so the output lands at
    # the path the canonical override mounts (./deploy/postgres/
    # relative to /opt/docker/shekel/docker-compose.override.yml).
    (cd "$PROD_ROOT" && sudo "$REPO_ROOT/scripts/generate_pg_cert.sh" --output-dir "$PROD_ROOT/deploy/postgres")
fi

# ── 2. Create secrets directory ───────────────────────────────────
log "Step 2: secrets directory"
if [[ ! -d "$PROD_ROOT/secrets" ]]; then
    install -d -m 0700 "$PROD_ROOT/secrets"
fi
[[ -d "$PROD_ROOT/secrets" ]] || die "could not create $PROD_ROOT/secrets"
chmod 0700 "$PROD_ROOT/secrets"

# ── 3. Migrate four secrets from .env to files ────────────────────
log "Step 3: migrate secrets to files"
extract_env_value() {
    local key=$1
    grep -E "^${key}=" "$PROD_ROOT/.env" | head -1 | cut -d= -f2-
}

for pair in \
    "SECRET_KEY:secret_key" \
    "POSTGRES_PASSWORD:postgres_password" \
    "APP_ROLE_PASSWORD:app_role_password" \
    "TOTP_ENCRYPTION_KEY:totp_encryption_key"
do
    env_key=${pair%%:*}
    file_name=${pair##*:}
    target="$PROD_ROOT/secrets/$file_name"
    if [[ -s "$target" ]]; then
        warn "$target already non-empty -- skipping"
        continue
    fi
    value=$(extract_env_value "$env_key")
    if [[ -z "$value" ]]; then
        die "$env_key missing or empty in $PROD_ROOT/.env -- cannot migrate to file"
    fi
    # ``printf '%s'`` writes the exact bytes with no trailing newline.
    # The entrypoint's _load_secret helper strips a trailing newline
    # for forgiveness, but printf-style is the documented form.
    printf '%s' "$value" > "$target"
    chmod 0600 "$target"
    echo "wrote $target ($(wc -c < "$target") bytes)"
done

# ── 4. Rewrite /opt/docker/shekel/.env ────────────────────────────
log "Step 4: rewrite $PROD_ROOT/.env"

# Snapshot the current digest pinned in /opt/docker/shekel/docker-compose.yml
# so the new SHEKEL_IMAGE_DIGEST line matches what's already running --
# we are NOT image-bumping as part of this reconciliation.
current_digest=$(grep -oE 'sha256:[0-9a-f]{64}' "$PROD_ROOT/docker-compose.yml" | grep -A1 saltyreformed | head -1 || true)
if [[ -z "$current_digest" ]]; then
    # Fallback: parse `image:` line directly.
    current_digest=$(grep -E 'image:.*saltyreformed/shekel' "$PROD_ROOT/docker-compose.yml" | grep -oE 'sha256:[0-9a-f]{64}' | head -1)
fi
[[ -n "$current_digest" ]] || die "could not extract current image digest from $PROD_ROOT/docker-compose.yml"

# Carry forward the values that should survive: SHEKEL_REDIS_PASSWORD,
# SEED_USER_*, GUNICORN_WORKERS, NGINX_PORT, AUDIT_RETENTION_DAYS,
# LOG_LEVEL, SLOW_REQUEST_THRESHOLD_MS, TOTP_ENCRYPTION_KEY_OLD.
SHEKEL_REDIS_PASSWORD=$(extract_env_value SHEKEL_REDIS_PASSWORD)
[[ -n "$SHEKEL_REDIS_PASSWORD" ]] || die "SHEKEL_REDIS_PASSWORD missing in current .env"
SEED_USER_EMAIL=$(extract_env_value SEED_USER_EMAIL)
SEED_USER_PASSWORD=$(extract_env_value SEED_USER_PASSWORD)
SEED_USER_DISPLAY_NAME=$(extract_env_value SEED_USER_DISPLAY_NAME)
GUNICORN_WORKERS=$(extract_env_value GUNICORN_WORKERS)
NGINX_PORT=$(extract_env_value NGINX_PORT)
AUDIT_RETENTION_DAYS=$(extract_env_value AUDIT_RETENTION_DAYS)
LOG_LEVEL=$(extract_env_value LOG_LEVEL)
SLOW_REQUEST_THRESHOLD_MS=$(extract_env_value SLOW_REQUEST_THRESHOLD_MS)

# Back up the existing .env before rewriting.
backup=$PROD_ROOT/.env.bak.$(date +%Y%m%d-%H%M%S)
cp "$PROD_ROOT/.env" "$backup"
chmod 0600 "$backup"
echo "backed up to $backup"

cat > "$PROD_ROOT/.env" <<ENVEOF
# Shekel Budget App -- shared-mode production environment.
# Rewritten by scripts/reconcile_prod_to_canonical.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Real secret values for SECRET_KEY, POSTGRES_PASSWORD,
# APP_ROLE_PASSWORD, and TOTP_ENCRYPTION_KEY are now in
# /opt/docker/shekel/secrets/<name>; the placeholders below satisfy
# the base docker-compose.yml's \${VAR:?...} interpolation.
# Audit findings F-022, F-109, F-148 / Commits C-34, C-38.

SECRET_KEY=replaced_by_docker_secret
POSTGRES_PASSWORD=replaced_by_docker_secret
APP_ROLE_PASSWORD=replaced_by_docker_secret
TOTP_ENCRYPTION_KEY=replaced_by_docker_secret

# Retired TOTP keys for non-destructive rotation.  Empty in steady
# state.  When set, also write the value to
# /opt/docker/shekel/secrets/totp_encryption_key_old and inline-add
# it to the secrets: block of the override during the rotation
# window.  See docs/runbook_secrets.md "Rotating TOTP_ENCRYPTION_KEY".
TOTP_ENCRYPTION_KEY_OLD=

# Per-app Redis ACL user password.  Lower sensitivity than the four
# secrets above (gates a Redis user restricted to the ~LIMITS*
# keyspace on an internal network), so kept as an env var.
SHEKEL_REDIS_PASSWORD=$SHEKEL_REDIS_PASSWORD

# Image digest pin.  Carried forward from the previous .env unchanged
# so this reconciliation does not double as an image bump.  Rotate
# via deploy/README.md "Image digest pinning" workflow.
SHEKEL_IMAGE_DIGEST=$current_digest

# Public-registration toggle.  Default false in production (audit
# finding F-053): the single-user budget app should not ship with
# an open registration surface.  /register returns 404 and the
# registration link is hidden on /login when this is false.
REGISTRATION_ENABLED=false

# Seed user (first-boot only; .env values are read once then scrubbed
# from the entrypoint's shell env).
SEED_USER_EMAIL=$SEED_USER_EMAIL
SEED_USER_PASSWORD=$SEED_USER_PASSWORD
SEED_USER_DISPLAY_NAME=$SEED_USER_DISPLAY_NAME

# Gunicorn / Nginx / observability tuneables.
GUNICORN_WORKERS=${GUNICORN_WORKERS:-2}
NGINX_PORT=${NGINX_PORT:-80}
AUDIT_RETENTION_DAYS=${AUDIT_RETENTION_DAYS:-365}
LOG_LEVEL=${LOG_LEVEL:-INFO}
SLOW_REQUEST_THRESHOLD_MS=${SLOW_REQUEST_THRESHOLD_MS:-500}
ENVEOF
chmod 0600 "$PROD_ROOT/.env"
echo "rewrote $PROD_ROOT/.env (backup at $backup)"

# ── 5. shekel-frontend network ────────────────────────────────────
log "Step 5: shekel-frontend network"
if docker network inspect shekel-frontend >/dev/null 2>&1; then
    echo "shekel-frontend already exists"
else
    docker network create shekel-frontend --driver bridge --subnet 172.32.0.0/24
fi

# ── 6. Shared Nginx vhost ─────────────────────────────────────────
log "Step 6: shared Nginx vhost"
# Snapshot the host nginx files BEFORE overwriting them.  The repo
# nginx-shared/nginx.conf has historically drifted behind the host
# (e.g. Audit B7 hardening added on the host first), so an unconditional
# repo->host sync can clobber load-bearing config.  Snapshot first,
# diff second, copy third.
nginx_snap=/opt/docker/nginx/.reconcile-snapshot-$(date +%Y%m%d-%H%M%S)
mkdir -p "$nginx_snap"
cp "$SHARED_NGINX_CONF_D/shekel.conf"    "$nginx_snap/shekel.conf.pre-reconcile" 2>/dev/null || true
cp /opt/docker/nginx/nginx.conf          "$nginx_snap/nginx.conf.pre-reconcile"
echo "nginx snapshot saved to $nginx_snap/"

if cmp -s "$REPO_ROOT/deploy/nginx-shared/conf.d/shekel.conf" "$SHARED_NGINX_CONF_D/shekel.conf"; then
    echo "$SHARED_NGINX_CONF_D/shekel.conf already matches repo -- skipping"
else
    cp -v "$REPO_ROOT/deploy/nginx-shared/conf.d/shekel.conf" "$SHARED_NGINX_CONF_D/shekel.conf"
    echo "(operator: validate + reload nginx after step 7)"
fi

# Also sync the main nginx.conf if it differs -- but FAIL LOUDLY if
# the host file contains directives the repo file does not (audit
# B7 limit_req_zone, etc.).  Pure visual diff for the operator to
# review before letting the sync proceed.
if cmp -s "$REPO_ROOT/deploy/nginx-shared/nginx.conf" /opt/docker/nginx/nginx.conf; then
    echo "/opt/docker/nginx/nginx.conf already matches repo"
else
    drift_dir=$nginx_snap/divergence
    mkdir -p "$drift_dir"
    diff -u /opt/docker/nginx/nginx.conf "$REPO_ROOT/deploy/nginx-shared/nginx.conf" > "$drift_dir/nginx.conf.diff" || true
    echo "Drift between host and repo nginx.conf saved to $drift_dir/nginx.conf.diff"
    echo "Critical directives that must NOT be lost in the sync:"
    grep -nE "limit_req_zone|limit_conn_zone|client_body_timeout|client_header_timeout|send_timeout|client_max_body_size|set_real_ip_from" /opt/docker/nginx/nginx.conf || true
    cp -v "$REPO_ROOT/deploy/nginx-shared/nginx.conf" /opt/docker/nginx/nginx.conf
fi

# ── 7. Copy compose files ─────────────────────────────────────────
log "Step 7: compose files"
# Snapshot the old files for rollback before overwriting.
snap_dir=$PROD_ROOT/.reconcile-snapshot-$(date +%Y%m%d-%H%M%S)
mkdir -p "$snap_dir"
cp "$PROD_ROOT/docker-compose.yml" "$snap_dir/docker-compose.yml.pre-reconcile"
cp "$PROD_ROOT/docker-compose.override.yml" "$snap_dir/docker-compose.override.yml.pre-reconcile"
echo "snapshot saved to $snap_dir/"

cp -v "$REPO_ROOT/docker-compose.yml" "$PROD_ROOT/docker-compose.yml"
cp -v "$REPO_ROOT/deploy/docker-compose.prod.yml" "$PROD_ROOT/docker-compose.override.yml"

# Validate the merged config before exiting.
log "Validation: docker compose config (merged)"
(cd "$PROD_ROOT" && docker compose config) > "$snap_dir/merged-config.yml"
echo "merged config rendered to $snap_dir/merged-config.yml"

# Sanity-spot key fields.
grep -qE '^    read_only: true$' "$snap_dir/merged-config.yml" || die "read_only field missing in merged config"
grep -qE '^      shekel-frontend:' "$snap_dir/merged-config.yml" || die "shekel-frontend network not attached in merged config"
grep -qE '^secrets:$' "$snap_dir/merged-config.yml" || die "secrets block missing in merged config"

cat <<EONEXT

────────────────────────────────────────────────────────────────────
RECONCILIATION COMPLETE -- pending recreate.

Spot-check the merged config:
    less $snap_dir/merged-config.yml

When ready to recreate the stack:

    # (a) Recreate the shared Nginx + cloudflared to pick up the
    #     shekel-frontend network attachment from /opt/docker/docker-compose.yml.
    cd /opt/docker
    docker compose up -d nginx cloudflared

    # (b) Validate Nginx is happy with the new vhost before reload.
    docker exec nginx nginx -t
    docker exec nginx nginx -s reload

    # (c) Recreate the Shekel stack.
    cd $PROD_ROOT
    docker compose down
    docker compose up -d

    # (d) Confirm health.
    docker compose ps
    docker compose logs --tail=80 app
    docker inspect shekel-prod-app --format '{{.HostConfig.ReadonlyRootfs}}'  # → true
    docker inspect shekel-prod-app --format '{{.HostConfig.CapDrop}}'         # → [ALL]
    curl -sI https://shekel.saltyreformed.com/login | head -5                  # → 200 + sec headers

Rollback (if anything goes wrong):

    sudo cp $snap_dir/docker-compose.yml.pre-reconcile $PROD_ROOT/docker-compose.yml
    sudo cp $snap_dir/docker-compose.override.yml.pre-reconcile $PROD_ROOT/docker-compose.override.yml
    sudo cp $backup $PROD_ROOT/.env
    cd $PROD_ROOT && docker compose up -d
────────────────────────────────────────────────────────────────────
EONEXT
