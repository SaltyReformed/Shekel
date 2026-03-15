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

# ── 5. Create seed user (first run only) ───────────────────────
if [ -n "$SEED_USER_EMAIL" ]; then
    echo "Checking for seed user..."
    python scripts/seed_user.py
fi

# ── 6. Seed tax brackets (requires users to exist) ─────────────
echo "Seeding tax configuration..."
python scripts/seed_tax_brackets.py
echo "Seeding complete."

echo "=== Starting Gunicorn ==="
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --access-logfile "" \
    --error-logfile - \
    --log-level info \
    run:app
