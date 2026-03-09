# Shekel Budget App — Multi-Stage Dockerfile
# Stage 1: Build Python dependencies (includes gcc for psycopg2).
# Stage 2: Slim runtime image (no build tools).

# ── Stage 1: Builder ────────────────────────────────────────────
FROM python:3.14-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# ── Stage 2: Runtime ────────────────────────────────────────────
FROM python:3.14-slim

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
COPY . .
COPY entrypoint.sh /home/shekel/app/entrypoint.sh

# Own everything as shekel user.
RUN chown -R shekel:shekel /home/shekel

USER shekel
EXPOSE 5000

ENTRYPOINT ["/home/shekel/app/entrypoint.sh"]
