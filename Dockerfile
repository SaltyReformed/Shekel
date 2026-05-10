# Shekel Budget App -- Multi-Stage Dockerfile
# Stage 1: Build Python dependencies (includes gcc for psycopg2).
# Stage 2: Slim runtime image (no build tools).

# ── Stage 1: Builder ────────────────────────────────────────────
# Pin to a specific patch version for reproducible builds.
# Update this version deliberately, not by accident via
# floating tags.  Last updated: 2026-03-22.
FROM python:3.14.3-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc libc-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# ── Stage 2: Runtime ────────────────────────────────────────────
FROM python:3.14.3-slim

# Runtime-only PostgreSQL client library + CLI tools for entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user.
RUN useradd --create-home shekel
WORKDIR /home/shekel/app

# Copy virtualenv from builder.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code and entrypoint.
COPY --chown=shekel:shekel . .
COPY --chown=shekel:shekel entrypoint.sh /home/shekel/app/entrypoint.sh

# Ensure the static-files mount point exists.  /var/www/static is a
# shared volume Nginx reads from.  Logs go to stdout (captured by
# Docker's json-file driver and shipped off-host by the Alloy
# collector documented in observability.md) so no /home/shekel/app/logs
# directory is created or written -- see Commit C-15 / findings F-082
# and F-150.
#
# /home/shekel/app/state is a small writable volume mount target the
# seed sentinel lives under.  Pre-creating the directory in the image
# guarantees it is shekel-owned when Docker first creates the volume
# (the volume inherits the contents and ownership of the underlying
# image path on first creation) so entrypoint.sh's ``touch`` runs as
# the unprivileged shekel user without an in-line chown step.  See
# audit finding F-022 and remediation Commit C-34.
RUN mkdir -p /var/www/static /home/shekel/app/state \
    && chown -R shekel:shekel /home/shekel/app /var/www/static

USER shekel
EXPOSE 8000

# Health check: verify the app is responding and database is reachable.
# Uses Python's built-in urllib (curl/wget are not in the slim image).
# --start-period gives entrypoint.sh time to run migrations and seeding
# (schema creation + Alembic + ref data + user + tax brackets can take
# well over 30 seconds on a fresh database).
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
