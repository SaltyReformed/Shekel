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
    echo "  - Missing or invalid values in .env (POSTGRES_PASSWORD, SECRET_KEY)"
    echo "  - Database migration conflict"
    echo ""
    echo "To view full logs:  docker logs shekel-app"
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

# ── 2. Create schemas ──────────────────────────────────────────
echo "Creating database schemas..."
PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -d "${DB_NAME:-shekel}" -f scripts/init_db.sql -q
echo "Schemas ready."

# ── 3. Initialize database (fresh) or run migrations (existing) ─
echo "Initializing database..."
python scripts/init_database.py

# ── 4. Seed reference data ─────────────────────────────────────
echo "Seeding reference data..."
python scripts/seed_ref_tables.py

# ── 5. Seed initial user (optional, first run only) ────────────
# Only runs if SEED_USER_EMAIL is set and non-empty.
# seed_user.py is idempotent -- skips if the user already exists.
# Alternative: leave SEED_USER_EMAIL empty and use /register instead.
# seed_user.py creates: user, settings, checking account, baseline
# scenario, and default categories.  It does NOT create tax data --
# that is handled by seed_tax_brackets.py in the next step.
#
# The /register web route creates all of the above PLUS tax data in
# a single transaction via auth_service.register_user().
if [ -n "${SEED_USER_EMAIL}" ]; then
    echo "Seeding initial user..."
    python scripts/seed_user.py
    echo "User seeding complete."
else
    echo "SEED_USER_EMAIL not set, skipping user seed. Use /register to create your account."
fi

# ── 6. Seed tax brackets ──────────────────────────────────────
echo "Seeding tax configuration..."
python scripts/seed_tax_brackets.py
echo "Seeding complete."

# ── 7. Copy static files to shared volume ────────────────────────
# Nginx serves /static/ directly from this volume.  Copying on every
# start ensures files are current after image updates.
echo "Copying static files to shared volume..."
cp -r /home/shekel/app/app/static/* /var/www/static/ 2>/dev/null || true
echo "Static files ready."

# ── 8. Start the application server ──────────────────────────────
# exec "$@" runs the Dockerfile CMD (gunicorn in production, or the
# docker-compose command override in development).
echo "=== Starting Application ==="
exec "$@"
