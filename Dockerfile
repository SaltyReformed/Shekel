# Shekel Budget App -- Multi-Stage Dockerfile
# Stage 1: Build Python dependencies (includes gcc for psycopg2).
# Stage 2: Slim runtime image (no build tools).
#
# BASE-IMAGE PINNING (audit findings F-025, F-060, F-062, F-120 / Commit C-36)
# ---------------------------------------------------------------------------
# Both stages pin the base image by sha256 digest, not by floating tag.
# The digest references the multi-arch image index for ``python:3.14-slim``
# rebuilt 2026-05-08, which carries:
#   * Python 3.14.4 (latest 3.14.x)
#   * Debian 13 (trixie) with libssl3t64 / openssl / openssl-provider-legacy
#     at 3.5.5-1~deb13u2 -- the post-CVE-2026-28390 (HIGH) fix
#     (audit F-025).
#   * pip 26.0.1 -- past the CVE-2026-1703 path-traversal fix
#     (audit F-120).
#
# The digest is the immutable identity; the ``:3.14-slim`` tag in the
# reference is informational so a casual reader can tell the line
# refers to the rolling 3.14.x slim variant.  When refreshing the
# digest:
#   1. Pull the new image:
#        docker pull python:3.14-slim
#   2. Capture the new index digest:
#        docker buildx imagetools inspect python:3.14-slim
#      The line ``Digest: sha256:...`` at the top is the OCI image
#      index digest; that is the value to paste below.
#   3. Verify the openssl/pip versions in the new image match or
#      exceed the constraints documented above.
#   4. Update the digest on BOTH FROM lines below in the same commit
#      so the builder and runtime stages stay in lockstep.
#
# OPENSSL DEFENSE-IN-DEPTH
# ------------------------
# Even with the digest pin, both stages run ``apt-get upgrade -y
# openssl libssl3t64 openssl-provider-legacy`` so a CVE that lands in
# Debian's trixie repos between digest refreshes is picked up on the
# next image build.  Belt-and-braces: the digest gives reproducibility,
# the apt upgrade gives currency.

# -- Stage 1: Builder -------------------------------------------------
FROM python:3.14-slim@sha256:1697e8e8d39bf168e177ac6b5fdab6df86d81cfc24dae17dfb96cfc3ef76b4dd AS builder

# Apply Debian security upgrades to the OpenSSL packages and install
# the build-only deps (libpq headers + a C toolchain) psycopg2 needs
# to compile from source.  Combined into a single RUN so the apt
# cache is removed in the same layer.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
        openssl libssl3t64 openssl-provider-legacy \
    && apt-get install -y --no-install-recommends \
        libpq-dev gcc libc-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade the system pip past the CVE-2026-1703 path-traversal fix
# (audit finding F-120).  The base image ships pip 26.0.1, but
# explicit upgrade defends against a future base-image regression.
# The upper bound prevents a major-version jump that could break
# the venv pip below.
RUN pip install --no-cache-dir --upgrade 'pip>=26.0,<27'

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# A fresh ensurepip-derived venv pip can lag the system pip by one
# release.  Re-run the upgrade inside the venv so /opt/venv ships
# with a CVE-fixed pip independent of the base image's pip.
RUN pip install --no-cache-dir --upgrade 'pip>=26.0,<27'

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# -- Stage 2: Runtime -------------------------------------------------
FROM python:3.14-slim@sha256:1697e8e8d39bf168e177ac6b5fdab6df86d81cfc24dae17dfb96cfc3ef76b4dd

# Apply the same Debian OpenSSL upgrade to the runtime stage.  The
# runtime image carries libssl3t64 (pulled in transitively by
# postgresql-client below); without this upgrade the CVE-fixed
# package would live only in the builder stage.  Runtime-only deps:
# libpq5 (psycopg2 runtime) and postgresql-client (psql in
# entrypoint.sh).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
        openssl libssl3t64 openssl-provider-legacy \
    && apt-get install -y --no-install-recommends \
        libpq5 postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user.
RUN useradd --create-home shekel
WORKDIR /home/shekel/app

# Copy virtualenv from builder.  The venv carries the CVE-fixed pip
# from stage 1 plus all production dependencies -- the runtime stage
# never invokes pip itself.
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
