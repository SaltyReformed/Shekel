#!/bin/bash
set -eEo pipefail

# ── Error handler ────────────────────────────────────────────────
# Prints troubleshooting guidance when any command fails.
# Fired by the ERR trap before set -e terminates the script.
entrypoint_failed() {
    local exit_code=$?
    echo ""
    echo "========================================"
    echo "  Shekel entrypoint failed (exit code: ${exit_code})"
    echo "========================================"
    echo ""
    echo "Check the output above for the specific error."
    echo ""
    echo "Common causes:"
    echo "  - PostgreSQL is not ready or not reachable"
    echo "  - Missing or invalid values in .env (POSTGRES_PASSWORD,"
    echo "    SECRET_KEY, APP_ROLE_PASSWORD)"
    echo "  - Database migration conflict"
    echo "  - Audit triggers absent or short of the expected count"
    echo ""
    echo "To view full logs:  docker logs shekel-prod-app"
    echo "Troubleshooting:    https://github.com/SaltyReformed/Shekel#troubleshooting"
    echo ""
}
trap entrypoint_failed ERR

echo "=== Shekel Entrypoint ==="

# ── 0. Validate SECRET_KEY shape ─────────────────────────────────
# Flask's ProdConfig.__init__ also validates SECRET_KEY, but it only
# fires once Python imports the config -- after entrypoint has already
# run migrations.  We catch misconfiguration here so the database is
# never touched under a placeholder key.  The placeholder list below
# must stay in sync with _KNOWN_DEFAULT_SECRETS in app/config.py.
# 32 is the minimum length matching _MIN_SECRET_KEY_LENGTH.
if [ -z "${SECRET_KEY}" ]; then
    echo "ERROR: SECRET_KEY is not set in the environment." >&2
    echo "       Generate with: python -c \"import secrets; print(secrets.token_hex(32))\"" >&2
    echo "       and add SECRET_KEY=<value> to .env before starting the app." >&2
    exit 1
fi
if [ "${#SECRET_KEY}" -lt 32 ]; then
    echo "ERROR: SECRET_KEY is shorter than 32 characters (got ${#SECRET_KEY})." >&2
    echo "       Generate a stronger key with: python -c \"import secrets; print(secrets.token_hex(32))\"" >&2
    exit 1
fi
case "${SECRET_KEY}" in
    dev-only-*|change-me-to-a-random-secret-key|dev-secret-key-not-for-production)
        echo "ERROR: SECRET_KEY matches a known placeholder." >&2
        echo "       Replace it with a secure random value before starting the app." >&2
        exit 1
        ;;
esac

# ── 0b. Validate APP_ROLE_PASSWORD ───────────────────────────────
# The least-privilege ``shekel_app`` role's password is required so
# the runtime app can connect under DML-only credentials.  See audit
# finding F-081 / Commit C-13.  The password is opaque to this script
# -- we only check that it is set and not a placeholder.  A sensible
# minimum length deters accidental short or empty values; the
# operator can use any sufficiently random secret.
if [ -z "${APP_ROLE_PASSWORD}" ]; then
    echo "ERROR: APP_ROLE_PASSWORD is not set in the environment." >&2
    echo "       Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
    echo "       and add APP_ROLE_PASSWORD=<value> to .env before starting the app." >&2
    exit 1
fi
if [ "${#APP_ROLE_PASSWORD}" -lt 16 ]; then
    echo "ERROR: APP_ROLE_PASSWORD is shorter than 16 characters (got ${#APP_ROLE_PASSWORD})." >&2
    echo "       Generate a stronger value with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
    exit 1
fi

# ── 1. Wait for PostgreSQL ──────────────────────────────────────
echo "Waiting for PostgreSQL..."
MAX_RETRIES=60
retries=0
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
    retries=$((retries + 1))
    if [ "${retries}" -ge "${MAX_RETRIES}" ]; then
        echo "ERROR: PostgreSQL not ready after ${MAX_RETRIES} seconds." >&2
        echo "Verify DB_HOST, DB_PORT, and DB_USER are correct and the database container is running." >&2
        exit 1
    fi
    sleep 1
done
echo "PostgreSQL is ready."

# Validate required environment variables.
if [ -z "${DB_PASSWORD}" ]; then
    echo "ERROR: DB_PASSWORD is not set. Set it in .env or docker-compose.yml."
    echo "       It must match POSTGRES_PASSWORD on the db service."
    exit 1
fi

# ── 2. Create schemas (init_db.sql) ──────────────────────────────
# init_db.sql is idempotent -- safe to run on every container start.
# Schema-only file; no psql variable substitution required so it is
# also compatible with the dev compose's
# /docker-entrypoint-initdb.d auto-init mount.
echo "Creating database schemas..."
PGPASSWORD="${DB_PASSWORD}" psql \
    -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" \
    -U "${DB_USER:-shekel_user}" -d "${DB_NAME:-shekel}" \
    -v ON_ERROR_STOP=1 \
    -f scripts/init_db.sql -q
echo "Schemas ready."

# ── 2b. Provision shekel_app least-privilege role ────────────────
# Separate file so init_db.sql can stay variable-free.  The variable
# name in psql -v is intentionally distinct from the env var
# (APP_ROLE_PASSWORD) so a casual reader of either file is not led
# to think the password is being interpolated by shell expansion --
# the boundary is explicit.
echo "Provisioning shekel_app role..."
PGPASSWORD="${DB_PASSWORD}" psql \
    -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" \
    -U "${DB_USER:-shekel_user}" -d "${DB_NAME:-shekel}" \
    -v "APP_ROLE_PASSWORD_LITERAL=${APP_ROLE_PASSWORD}" \
    -v ON_ERROR_STOP=1 \
    -f scripts/init_db_role.sql -q
echo "Role ready."

# ── 3. Initialize database (fresh) or run migrations (existing) ─
# init_database.py pops DATABASE_URL_APP from os.environ at startup
# so it always runs as the owner role (DATABASE_URL).  Migrations
# need DDL privileges; the app role has DML only.
echo "Initializing database..."
python scripts/init_database.py

# ── 4. Seed reference data ─────────────────────────────────────
echo "Seeding reference data..."
python scripts/seed_ref_tables.py

# ── 5. Seed initial user (optional, first run only) ────────────
# Only runs if SEED_USER_EMAIL is set and non-empty AND the seed
# sentinel file is absent.  seed_user.py is itself idempotent at the
# database level (skips when the user row exists), so the sentinel
# is purely a noise-reduction measure: it spares the operator a
# "User already exists" log line on every container restart.
#
# Seed sentinel path:
#   The sentinel lives in a writable named volume mounted at
#   /home/shekel/app/state.  A writable mount is required because
#   the production rootfs may be ``read_only: true`` (planned
#   Commit C-35).  The volume persists across container restarts so
#   the sentinel survives ``docker compose restart app``; recreating
#   the volume (e.g. operator-driven cleanup) re-runs the seed
#   script's idempotent path on the next start.
#
# Alternative to seed-script: leave SEED_USER_EMAIL empty and use
# /register instead.  seed_user.py creates: user, settings, checking
# account, baseline scenario, and default categories.  It does NOT
# create tax data -- that is handled by seed_tax_brackets.py in the
# next step.  The /register web route creates all of the above PLUS
# tax data in a single transaction via auth_service.register_user().
SEED_STATE_DIR="/home/shekel/app/state"
SEED_SENTINEL="${SEED_STATE_DIR}/.seed-complete"
if [ -n "${SEED_USER_EMAIL}" ]; then
    if [ -f "${SEED_SENTINEL}" ]; then
        echo "Seed sentinel present at ${SEED_SENTINEL}; skipping user seed step."
    else
        echo "Seeding initial user..."
        python scripts/seed_user.py
        echo "User seeding complete."
        # Materialise the sentinel only after the seed script
        # returned cleanly -- ``set -e`` above would have aborted the
        # entire entrypoint on non-zero, but the explicit ordering
        # also documents the contract: the sentinel records SUCCESS,
        # never the mere fact that we attempted to seed.  ``mkdir -p``
        # tolerates the directory already existing (initial volume
        # bring-up on a fresh deploy) without failing under set -e.
        mkdir -p "${SEED_STATE_DIR}"
        : >"${SEED_SENTINEL}"
    fi
else
    echo "SEED_USER_EMAIL not set, skipping user seed. Use /register to create your account."
fi

# ── 5b. Scrub seed credentials from the entrypoint env ──────────
# After the seed step completes (or was skipped because the sentinel
# was present), unset the SEED_USER_* variables so Gunicorn does not
# inherit them in /proc/<pid>/environ.  Audit finding F-022 -- the
# password is a one-shot bootstrapping credential; keeping it in the
# long-running app process's env serves no operational purpose and
# any container-escape or in-container RCE could read it back via
# /proc.  ``unset`` removes the names from the shell's exported
# environment list, so subsequent ``exec`` calls below pass a stripped
# environ array to their child.
#
# Caveat: this scrub closes the in-process channel only.  ``docker
# exec shekel-prod-app env`` reads from the container's stored
# Config.Env (set at container creation time) and is NOT affected by
# this unset.  Closing that channel requires migrating the seed
# credential to a Docker secret or first-run env_file -- planned for
# Commit C-38.
unset SEED_USER_PASSWORD SEED_USER_EMAIL SEED_USER_DISPLAY_NAME

# ── 6. Seed tax brackets ──────────────────────────────────────
echo "Seeding tax configuration..."
python scripts/seed_tax_brackets.py
echo "Seeding complete."

# ── 7. Verify audit triggers exist ───────────────────────────────
# Refuse to start Gunicorn if the rebuild migration did not
# materialise the expected number of audit triggers.  The expected
# count is sourced from app.audit_infrastructure.EXPECTED_TRIGGER_COUNT
# so an additional audited table only needs an edit there -- no
# parallel constant in shell.  See audit finding F-028 / Commit C-13.
EXPECTED_TRIGGERS=$(python -c \
    "from app.audit_infrastructure import EXPECTED_TRIGGER_COUNT; \
print(EXPECTED_TRIGGER_COUNT)")
ACTUAL_TRIGGERS=$(PGPASSWORD="${DB_PASSWORD}" psql \
    -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" \
    -U "${DB_USER:-shekel_user}" -d "${DB_NAME:-shekel}" \
    -tAc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'audit_%' AND NOT tgisinternal")
if [ "${ACTUAL_TRIGGERS}" -lt "${EXPECTED_TRIGGERS}" ]; then
    echo "ERROR: Audit trigger health check failed." >&2
    echo "       Expected at least ${EXPECTED_TRIGGERS} triggers, found ${ACTUAL_TRIGGERS}." >&2
    echo "       Run 'flask db upgrade' to apply the audit rebuild migration," >&2
    echo "       or check that scripts/init_database.py completed cleanly." >&2
    exit 1
fi
echo "Audit trigger health OK: ${ACTUAL_TRIGGERS} triggers (expected >= ${EXPECTED_TRIGGERS})."

# ── 8. Copy static files to shared volume ────────────────────────
# Nginx serves /static/ directly from this volume.  Copying on every
# start ensures files are current after image updates.
echo "Copying static files to shared volume..."
cp -r /home/shekel/app/app/static/* /var/www/static/ 2>/dev/null || true
echo "Static files ready."

# ── 9. Start the application server ──────────────────────────────
# Construct the DATABASE_URL_APP just-in-time so seed scripts above
# (which used DATABASE_URL = owner) are unaffected.  app/config.py
# prefers DATABASE_URL_APP over DATABASE_URL, so the gunicorn
# process inherits least-privilege credentials while the deployment
# scripts that already ran kept owner credentials.
#
# DB_SSLMODE is set on the shared-mode prod override only
# (deploy/docker-compose.prod.yml) -- audit finding F-154 / Commit
# C-37.  Bundled mode keeps the env var unset and DATABASE_URL_APP
# falls back to the historical plaintext form so the Quick Start
# in the README continues to work without operator-generated
# certs.  The value is appended verbatim as the ``sslmode``
# query parameter; valid libpq settings are
# disable / allow / prefer / require / verify-ca / verify-full.
DB_SSLMODE_QUERY=""
if [ -n "${DB_SSLMODE:-}" ]; then
    DB_SSLMODE_QUERY="?sslmode=${DB_SSLMODE}"
fi
export DATABASE_URL_APP="postgresql://shekel_app:${APP_ROLE_PASSWORD}@${DB_HOST:-db}:${DB_PORT:-5432}/${DB_NAME:-shekel}${DB_SSLMODE_QUERY}"

# exec "$@" runs the Dockerfile CMD (gunicorn in production, or the
# docker-compose command override in development).
echo "=== Starting Application ==="
exec "$@"
