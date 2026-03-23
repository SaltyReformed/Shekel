# Shekel Budget App -- Multi-Stage Dockerfile
# Stage 1: Build Python dependencies (includes gcc for psycopg2).
# Stage 2: Slim runtime image (no build tools).

# ── Stage 1: Builder ────────────────────────────────────────────
# Pin to a specific patch version for reproducible builds.
# Update this version deliberately, not by accident via
# floating tags.  Last updated: 2026-03-22.
FROM python:3.14.3-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
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

# Ensure writable directories exist for logs and shared static files.
# /var/www/static is a shared volume mount point -- Nginx reads from it.
RUN mkdir -p /home/shekel/app/logs /var/www/static \
    && chown -R shekel:shekel /home/shekel/app /var/www/static

USER shekel
EXPOSE 8000

# Health check: verify the app is responding and database is reachable.
# Uses Python's built-in urllib (curl/wget are not in the slim image).
# --start-period gives entrypoint.sh time to run migrations and seeding.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
CMD ["gunicorn", "--config", "gunicorn.conf.py", "run:app"]
