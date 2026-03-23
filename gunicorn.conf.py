"""
Shekel Budget App -- Gunicorn Configuration

Production WSGI server settings for running behind Nginx.
All values can be overridden via environment variables.

Usage:
    gunicorn --config gunicorn.conf.py run:app
"""

import os


# ── Binding ──────────────────────────────────────────────────────
# Listen on all interfaces, port 8000.  Nginx reverse-proxies to
# this port.  Not exposed externally -- only reachable on the Docker
# backend network.
bind = f"0.0.0.0:{os.getenv('GUNICORN_PORT', '8000')}"

# ── Workers ──────────────────────────────────────────────────────
# Number of worker processes.  2 is appropriate for a single-user
# personal finance app on modest hardware (Proxmox VM).
# Formula for higher load: (2 * CPU cores) + 1.
workers = int(os.getenv("GUNICORN_WORKERS", "2"))

# ── Timeouts ─────────────────────────────────────────────────────
# Seconds to wait for a worker to finish handling a request.
# 120s accommodates slow operations like 2-year recurrence
# regeneration.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))

# Seconds to wait for a worker to gracefully shut down after
# receiving a restart signal (SIGHUP).
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "120"))

# Seconds to wait for the next request on a Keep-Alive connection.
# Slightly higher than Nginx's keepalive_timeout (65s) to let Nginx
# close idle connections first, avoiding race conditions.
keepalive = 5

# ── Logging ──────────────────────────────────────────────────────
# Access log: DISABLED.  Flask's after_request middleware in
# app/utils/logging_config.py already logs every request with
# structured JSON fields (request_id, duration, user_id, method,
# path, status).  Enabling Gunicorn's access log would produce
# duplicate request entries in container stdout.
accesslog = None

# Error log: sent to stderr, captured by Docker as container logs.
# Covers startup messages, worker lifecycle events, and unhandled
# exceptions.  Low-volume output.
errorlog = "-"

# Log level for Gunicorn's own process-level messages.
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# ── Process Naming ───────────────────────────────────────────────
# Identifies the master and worker processes in `ps` output.
proc_name = "shekel"

# ── Request Limits ───────────────────────────────────────────────
# Maximum size of the HTTP request line (URL + query string).
limit_request_line = 8190

# Maximum number of HTTP request headers.
limit_request_fields = 100

# Maximum size of a single HTTP request header.
limit_request_field_size = 8190

# ── Forwarded Headers ────────────────────────────────────────────
# Trust X-Forwarded-* headers only from RFC 1918 private subnets,
# which covers all Docker bridge networks.  Override via the
# FORWARDED_ALLOW_IPS environment variable if the architecture
# changes (e.g., non-RFC1918 reverse proxy).
#
# IMPORTANT: Do NOT set to "*" unless Gunicorn is behind a trusted
# reverse proxy on a private network with no external access.
forwarded_allow_ips = os.getenv(
    "FORWARDED_ALLOW_IPS",
    "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8",
)
