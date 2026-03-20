#!/bin/bash
set -e

echo "=== Shekel Entrypoint ==="

# ── 1. Wait for PostgreSQL ──────────────────────────────────────
echo "Waiting for PostgreSQL..."
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
    sleep 1
done
echo "PostgreSQL is ready."

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

# ── 5. Seed tax brackets ──────────────────────────────────────
echo "Seeding tax configuration..."
python scripts/seed_tax_brackets.py
echo "Seeding complete."

# ── 6. Copy static files to shared volume ────────────────────────
# Nginx serves /static/ directly from this volume.  Copying on every
# start ensures files are current after image updates.
echo "Copying static files to shared volume..."
cp -r /home/shekel/app/app/static/* /var/www/static/ 2>/dev/null || true
echo "Static files ready."

# ── 7. Start the application server ──────────────────────────────
# exec "$@" runs the Dockerfile CMD (gunicorn in production, or the
# docker-compose command override in development).
echo "=== Starting Application ==="
exec "$@"
