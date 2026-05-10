#!/bin/bash
# Shekel Budget App -- Generate PostgreSQL TLS certificate
#
# Generates a self-signed X.509 certificate and RSA private key for
# the shared-mode Postgres service in deploy/docker-compose.prod.yml
# (audit finding F-154 / Commit C-37).  The output files are mounted
# read-only into the postgres:16-alpine container at
# /etc/postgresql/certs/server.{crt,key} and read by the postgres
# process at startup via the ``ssl_cert_file`` / ``ssl_key_file``
# directives in the override's ``services.db.command`` block.
#
# Why self-signed: the Gunicorn -> Postgres hop stays inside a single
# internal Docker bridge (``backend``) that no other tenant can reach,
# so a public CA chain adds no security value but a real operational
# cost (cert renewal, ACME bootstrap inside the container network).
# psycopg2 is configured with ``sslmode=require`` -- the channel is
# encrypted and Postgres still authenticates the client password but
# the cert chain itself is not validated.  Upgrading to
# ``sslmode=verify-ca`` or ``verify-full`` requires committing
# ``deploy/postgres/server.crt`` (already public knowledge -- it is
# bind-mounted into the container) as the CA pem and shipping it to
# the app container; that is a future hardening step, not the
# C-37 baseline.
#
# Usage:
#     sudo ./scripts/generate_pg_cert.sh [OPTIONS]
#
# Sudo is required because the generated key file MUST be chowned to
# the postgres user inside the postgres:16-alpine image (uid 70) for
# Postgres to accept it under the mandatory 0600 mode.
#
# Options:
#     --output-dir DIR    Output directory (default: deploy/postgres)
#     --days N            Certificate validity in days (default: 825)
#     --cn HOSTNAME       Certificate Common Name (default: shekel-prod-db)
#     --postgres-uid UID  uid to own the key file (default: 70)
#     --postgres-gid GID  gid to own the key file (default: 70)
#     --force             Overwrite existing cert/key without prompting
#     --help              Show this help message
#
# Exit codes:
#     0   Cert and key generated (or already present and -f not set)
#     1   Fatal error (missing dependency, openssl failure, chown failure)
#     2   Output already exists and --force was not passed
#
# Why 825 days: the modern Apple / Mozilla root program limits accepted
# server cert lifetimes to 398 days for trusted CAs; that limit does
# not apply to a private self-signed cert because no browser ever
# validates it.  825 is the historical CA/B Forum max-lifetime ceiling,
# which keeps ``openssl x509 -checkend`` warnings predictable: the
# operator gets a nudge to rotate every ~2 years, well before the
# practical libssl ceiling.  The operator may pass ``--days 365`` to
# match a stricter rotation cadence.

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────
# Resolve REPO_ROOT relative to this script so the operator can
# invoke it from any working directory.  realpath -m tolerates the
# parent directory not yet existing (it never doesn't, but this
# matches the defensive style of the other scripts).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

OUTPUT_DIR="${REPO_ROOT}/deploy/postgres"
DAYS="825"
CN="shekel-prod-db"
# postgres:16-alpine ships the postgres user at uid 70 (musl
# convention).  The base docker-compose.yml pins ``user: postgres``
# on the db service so the postgres process runs as uid 70 even
# under cap_drop ALL.  The bind-mounted private key must be owned
# by that uid for Postgres to accept it under the mandatory 0600
# mode -- the postgres source explicitly checks this in
# ``be-secure-openssl.c::be_tls_init``.
POSTGRES_UID="70"
POSTGRES_GID="70"
FORCE="false"

# ── Functions ────────────────────────────────────────────────────

log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: sudo $(basename "$0") [OPTIONS]

Generate a self-signed PostgreSQL TLS cert + key for the shared-mode
deployment (audit finding F-154 / Commit C-37).

Options:
    --output-dir DIR    Output directory (default: deploy/postgres)
    --days N            Certificate validity in days (default: 825)
    --cn HOSTNAME       Certificate Common Name (default: shekel-prod-db)
    --postgres-uid UID  uid to own the key file (default: 70)
    --postgres-gid GID  gid to own the key file (default: 70)
    --force             Overwrite existing cert/key without prompting
    --help              Show this help message

Output files (under --output-dir):
    server.crt          Self-signed cert, mode 0644 owned by root
    server.key          RSA-2048 private key, mode 0600 owned by uid 70

The output files are bind-mounted read-only into shekel-prod-db at
/etc/postgresql/certs/.  See deploy/docker-compose.prod.yml for the
volume declarations and the matching ssl_cert_file / ssl_key_file
postgres command-line directives.

Examples:
    sudo $(basename "$0")
        # Generate with defaults; abort if cert/key already exist.

    sudo $(basename "$0") --force --days 365
        # Rotate to a fresh 1-year cert.

    sudo $(basename "$0") --cn db.example.internal
        # Embed a custom CN (display only -- psycopg2 sslmode=require
        # does not validate the CN).
EOF
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --output-dir)
                if [ -z "${2:-}" ]; then
                    log "ERROR" "--output-dir requires a directory argument"
                    exit 1
                fi
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --days)
                if [ -z "${2:-}" ]; then
                    log "ERROR" "--days requires a numeric argument"
                    exit 1
                fi
                if ! [[ "$2" =~ ^[0-9]+$ ]]; then
                    log "ERROR" "--days must be a positive integer (got: $2)"
                    exit 1
                fi
                if [ "$2" -lt 1 ]; then
                    log "ERROR" "--days must be >= 1 (got: $2)"
                    exit 1
                fi
                DAYS="$2"
                shift 2
                ;;
            --cn)
                if [ -z "${2:-}" ]; then
                    log "ERROR" "--cn requires a hostname argument"
                    exit 1
                fi
                CN="$2"
                shift 2
                ;;
            --postgres-uid)
                if [ -z "${2:-}" ]; then
                    log "ERROR" "--postgres-uid requires a numeric argument"
                    exit 1
                fi
                if ! [[ "$2" =~ ^[0-9]+$ ]]; then
                    log "ERROR" "--postgres-uid must be a non-negative integer (got: $2)"
                    exit 1
                fi
                POSTGRES_UID="$2"
                shift 2
                ;;
            --postgres-gid)
                if [ -z "${2:-}" ]; then
                    log "ERROR" "--postgres-gid requires a numeric argument"
                    exit 1
                fi
                if ! [[ "$2" =~ ^[0-9]+$ ]]; then
                    log "ERROR" "--postgres-gid must be a non-negative integer (got: $2)"
                    exit 1
                fi
                POSTGRES_GID="$2"
                shift 2
                ;;
            --force)
                FORCE="true"
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                log "ERROR" "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done
}

check_prerequisites() {
    # openssl is the canonical cert generation tool and is part of
    # the Arch base; the Dockerfile also depends on it for cosign +
    # the audit-window scans.  A missing openssl means we cannot
    # produce a cert.
    if ! command -v openssl &>/dev/null; then
        log "ERROR" "openssl not found on PATH"
        log "ERROR" "Install with: sudo pacman -S openssl"
        exit 1
    fi

    # chown to a non-root uid requires root privileges.  Refuse
    # outright rather than emit a confusing chown EPERM later.
    # The script's only side effects before this point are
    # in-memory parsing; aborting here is safe.
    if [ "$(id -u)" -ne 0 ]; then
        log "ERROR" "must be run as root (or via sudo) so the generated key can be chowned to uid ${POSTGRES_UID}"
        log "ERROR" "Re-run with: sudo $(basename "$0") $*"
        exit 1
    fi
}

check_existing_files() {
    # Idempotent behaviour: if both the cert and key already exist,
    # report and exit 0 unless --force is set.  This lets the
    # operator wire the script into a one-shot bring-up Makefile
    # without re-keying the database on every invocation.
    local cert_path="${OUTPUT_DIR}/server.crt"
    local key_path="${OUTPUT_DIR}/server.key"

    if [ -f "${cert_path}" ] || [ -f "${key_path}" ]; then
        if [ "${FORCE}" != "true" ]; then
            log "WARN" "${cert_path} or ${key_path} already exists"
            log "WARN" "Re-run with --force to overwrite (rotates the cert)"
            log "WARN" "After rotation, restart the db container so postgres re-reads the cert:"
            log "WARN" "    docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart db"
            exit 2
        fi
        log "INFO" "--force set; overwriting existing cert/key in ${OUTPUT_DIR}"
    fi
}

generate_cert_and_key() {
    local cert_path="${OUTPUT_DIR}/server.crt"
    local key_path="${OUTPUT_DIR}/server.key"

    log "INFO" "Generating Postgres TLS cert + key in ${OUTPUT_DIR}"
    log "INFO" "  CN:        ${CN}"
    log "INFO" "  Days:      ${DAYS}"
    log "INFO" "  Key owner: uid=${POSTGRES_UID} gid=${POSTGRES_GID}"

    # Create the output directory if it does not exist.  install -d
    # is preferred over ``mkdir -p`` because it normalizes the mode
    # in a single call -- 0755 lets the postgres user (uid 70) read
    # but not write the directory, matching the read-only bind
    # mount in the compose override.
    install -d -m 0755 "${OUTPUT_DIR}"

    # Generate cert + key in a single openssl req invocation.
    #
    # Flags:
    #   -x509           Output a self-signed cert (not a CSR).
    #   -nodes          Do not encrypt the private key with a passphrase.
    #                   Postgres reads the key at startup; an encrypted
    #                   key would require interactive prompting that
    #                   the docker-entrypoint cannot satisfy.
    #   -newkey rsa:2048
    #                   Fresh RSA-2048 keypair.  RSA chosen over
    #                   ECDSA because libpq's TLS support varies by
    #                   client version and RSA is the lowest-common-
    #                   denominator that every supported psycopg2
    #                   release accepts under sslmode=require.
    #   -days ${DAYS}   Validity window.
    #   -subj /CN=...   Distinguished Name.  Only the CN is set;
    #                   psycopg2 with sslmode=require does not
    #                   validate the CN, but a meaningful value
    #                   makes ``openssl x509 -text -in server.crt``
    #                   greppable for the deployment hostname.
    #   -addext subjectAltName=DNS:...
    #                   Add a SAN matching the CN so a future
    #                   upgrade to sslmode=verify-full works
    #                   without regenerating the cert.  The SAN
    #                   list also covers the docker DNS short
    #                   name (``db``) used by the in-stack app.
    #   -addext keyUsage=...
    #                   Restrict the key to the operations Postgres
    #                   actually performs (digital signatures and
    #                   key encipherment).  Defense in depth: a
    #                   leaked key cannot be repurposed for client
    #                   auth.
    #   -addext extendedKeyUsage=serverAuth
    #                   Mark the cert for server-auth use only.
    openssl req \
        -x509 \
        -nodes \
        -newkey "rsa:2048" \
        -days "${DAYS}" \
        -keyout "${key_path}" \
        -out "${cert_path}" \
        -subj "/CN=${CN}" \
        -addext "subjectAltName=DNS:${CN},DNS:db,DNS:shekel-prod-db,DNS:localhost" \
        -addext "keyUsage=digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth" \
        2>/dev/null

    # File modes:
    #   server.crt: 0644 (world readable -- the cert is public).
    #   server.key: 0600 (Postgres rejects a wider mode in
    #               be_tls_init's stat() check).
    chmod 0644 "${cert_path}"
    chmod 0600 "${key_path}"

    # Ownership:
    #   server.crt stays root-owned (read by all, written by no one).
    #   server.key must be owned by the user the postgres process
    #               runs as (uid 70 in postgres:16-alpine) so the
    #               in-container postgres can read it.  The bind
    #               mount preserves host ownership exactly.
    chown "${POSTGRES_UID}:${POSTGRES_GID}" "${key_path}"

    log "INFO" "Generated:"
    log "INFO" "  ${cert_path} ($(stat -c '%a' "${cert_path}") $(stat -c '%U:%G' "${cert_path}"))"
    log "INFO" "  ${key_path} ($(stat -c '%a' "${key_path}") $(stat -c '%U:%G' "${key_path}"))"
}

verify_output() {
    local cert_path="${OUTPUT_DIR}/server.crt"
    local key_path="${OUTPUT_DIR}/server.key"

    # Sanity check: openssl re-reads the cert and confirms it
    # parses cleanly.  A corrupt write surfaces here, before the
    # operator restarts Postgres and gets a more obscure
    # ``could not load private key file`` from the entrypoint.
    if ! openssl x509 -in "${cert_path}" -noout -text >/dev/null 2>&1; then
        log "ERROR" "generated cert at ${cert_path} fails openssl x509 parse"
        exit 1
    fi
    if ! openssl rsa -in "${key_path}" -check -noout >/dev/null 2>&1; then
        log "ERROR" "generated key at ${key_path} fails openssl rsa check"
        exit 1
    fi

    # Confirm the cert is self-signed by re-deriving the public
    # key from the key file and comparing it to the cert's
    # subject public key.  This catches a swap between cert and
    # key files (rare, but possible if the operator manually
    # edits before re-running this script).
    local cert_pubkey key_pubkey
    cert_pubkey="$(openssl x509 -in "${cert_path}" -pubkey -noout 2>/dev/null)"
    key_pubkey="$(openssl rsa -in "${key_path}" -pubout 2>/dev/null)"
    if [ "${cert_pubkey}" != "${key_pubkey}" ]; then
        log "ERROR" "cert and key public keys do not match"
        log "ERROR" "  ${cert_path}"
        log "ERROR" "  ${key_path}"
        exit 1
    fi

    # Echo the not-before / not-after window so the operator can
    # diary a rotation date.
    local not_after
    not_after="$(openssl x509 -in "${cert_path}" -noout -enddate | sed 's/^notAfter=//')"
    log "INFO" "Certificate valid until: ${not_after}"
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    parse_args "$@"
    check_prerequisites "$@"
    check_existing_files
    generate_cert_and_key
    verify_output
    log "INFO" "Done."
    log "INFO" "Next: bring up the db service with the shared-mode override:"
    log "INFO" "    docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d db"
}

main "$@"
