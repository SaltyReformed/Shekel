#!/bin/bash
set -e

echo "=== Shekel Entrypoint ==="

# ── 1. Wait for PostgreSQL ──────────────────────────────────────
echo "Waiting for PostgreSQL..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
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
