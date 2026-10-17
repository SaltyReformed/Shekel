#!/bin/bash
# Shekel Budget App -- Shared helpers for the backup family.
# SOURCE this file (". _backup_lib.sh"); do not execute it.
#
# Single home for the concepts backup.sh, restore.sh, verify_backup.sh,
# and backup_retention.sh previously each hand-maintained (polyglot audit
# 2026-06-12, finding OPS/SH-06 in docs/audits/polyglot-cleanup/findings.md):
# the application schema set (three divergent copies meant a new schema
# missed in backup.sh would silently drop that schema from every backup,
# discovered only at restore time), the log() function, the docker
# prerequisite stanza, the backup key file, and the pipelines that write and
# read an artifact.  Their settings and defaults are _settings_lib.sh's,
# which this file loads for them.
#
# errexit discipline: every helper uses explicit `if ! cmd; return 1`
# control flow instead of relying on set -e, because callers routinely
# invoke helpers in `cmd || rc=$?` contexts where bash DISABLES errexit
# for the entire function body -- the exact mechanism behind audit
# findings OPS/SH-03 and OPS/SH-07.  The exceptions EXIT the script
# themselves and are called as plain statements, never in a condition:
# refuse_retired_passphrase_variable, require_backup_key and
# write_dump_artifact (plan step balance:X-do).

# ── Schema set (single source of truth) ──────────────────────────
# public  -- alembic_version (migration state); always exists, never CREATEd
# ref     -- lookup/reference tables
# auth    -- users, sessions, MFA
# budget  -- pay periods, transactions, accounts, templates
# salary  -- salary profiles, deductions, tax configs
# system  -- audit_log
SHEKEL_DUMP_SCHEMAS=(public ref auth budget salary system)
SHEKEL_APP_SCHEMAS=(ref auth budget salary system)

# Echo the CREATE SCHEMA IF NOT EXISTS statements for the app schemas
# (public is owned by postgres and always present).
shekel_create_schema_sql() {
    local schema
    for schema in "${SHEKEL_APP_SCHEMAS[@]}"; do
        printf 'CREATE SCHEMA IF NOT EXISTS %s; ' "${schema}"
    done
}

# ── Logging ──────────────────────────────────────────────────────
log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    # shellcheck disable=SC2312 # date with a literal format string always succeeds; the timestamp is display-only inside this log line, never fed to a downstream operation
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

# ── Prerequisites ────────────────────────────────────────────────
# The docker facts the shell scripts share -- is a container running, the
# production containers' names -- have one home, shared with
# deploy/shekel-deploy.sh (plan steps balance:X-dm and balance:X-do, ruling
# R-BAL254).  A copy of it lacking a function the backup family reads is
# refused here, before any of them touches anything: a missing
# container_running would otherwise fail as "command not found" and read as
# "the database container is not running" (finding BAL-625).
_docker_lib="$(dirname "${BASH_SOURCE[0]}")/_docker_lib.sh"
# shellcheck source=_docker_lib.sh
if ! {
    source "${_docker_lib}" \
        && declare -F container_running >/dev/null
}; then
    log "ERROR" "Cannot load ${_docker_lib}, or it lacks a function the backup"
    log "ERROR" "scripts read (container_running).  It must come from this file's revision."
    exit 1
fi

# ── Settings ─────────────────────────────────────────────────────
# A backup script declares the settings it reads with read_settings and prints
# them in its --help with print_settings; each default's one home is the
# table in _settings_lib.sh.
# shellcheck source=_settings_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_settings_lib.sh"

# Verify docker exists and the named DB container is running.
# Usage: require_db_container "$DB_CONTAINER" || exit 1
require_db_container() {
    local container="$1"
    if ! command -v docker &>/dev/null; then
        log "ERROR" "docker command not found"
        return 1
    fi
    # shellcheck disable=SC2310 # container_running is a boolean predicate (one captured docker inspect); its 0/1 is exactly what this if tests.
    if ! container_running "${container}"; then
        log "ERROR" "Database container '${container}' is not running"
        return 1
    fi
    return 0
}

# ── The backup key ───────────────────────────────────────────────
# Every encrypted artifact is written and read with ONE passphrase, the first
# line of ONE file at a fixed path, with no setting to move it (Josh's rulings
# R-BAL304, R-BAL305 and R-BAL306): a cron line carries no secret, and nothing
# can point the scripts at a second copy.  gpg reads the file itself
# (--batch --passphrase-file, measured on gpg 2.4.9 on 2026-10-16: no
# loopback pinentry needed, only the FIRST line read), so the passphrase is
# on no pipe, in no argv and in no shell variable.  Plain artifacts need no key: a .gpg name is what marks an
# encrypted one, on the way in and on the way out.
SHEKEL_BACKUP_KEY_FILE=/etc/shekel/backup-passphrase

# The variable the passphrase used to arrive in.  Set at all -- empty
# included -- it is refused before anything else, naming the file: an install
# still setting it would otherwise go on carrying the secret in a crontab or
# a profile that nothing reads.
refuse_retired_passphrase_variable() {
    if [[ -n "${BACKUP_ENCRYPTION_PASSPHRASE+x}" ]]; then
        log "ERROR" "BACKUP_ENCRYPTION_PASSPHRASE is set, but no backup script reads it:"
        log "ERROR" "the passphrase is the first line of ${SHEKEL_BACKUP_KEY_FILE}.  Write it"
        log "ERROR" "there, then remove the variable from wherever it is set (a crontab"
        log "ERROR" "line, a shell profile, a wrapper).  docs/backup_runbook.md, section 3.3."
        exit 1
    fi
}

# Refuse the key file unless it is the owner's alone: a regular file judged
# by lstat (a symbolic link is refused, whatever it points at), owned by the
# user running the script, no permission bit for the group or others, and a
# first line holding a character (what gpg reads; only that one character is
# read, so not even an xtrace prints the passphrase).  $1, when given, is
# what the caller can do instead (a flag that needs no key).
require_backup_key() {
    local key="${SHEKEL_BACKUP_KEY_FILE}" instead="${1:-}" problem="" mode
    local first_char=""
    if [[ -L "${key}" ]]; then
        problem="it is a symbolic link"
    elif [[ ! -e "${key}" ]]; then
        problem="it does not exist"
    elif [[ ! -f "${key}" ]]; then
        problem="it is not a regular file"
    elif [[ ! -O "${key}" ]]; then
        problem="it is not owned by uid ${EUID}, the user running this script"
    elif ! mode=$(stat -c '%a' -- "${key}"); then
        problem="its mode cannot be read"
    elif ((8#${mode} & 8#077)); then
        problem="its mode ${mode} gives the group or other users access"
    else
        # One character, or none when the first line is empty (read stops at
        # the newline); an unreadable file leaves it empty too.
        IFS= read -r -n 1 first_char 2>/dev/null <"${key}" || true
        if [[ -z "${first_char}" ]]; then
            problem="its first line is empty, or it cannot be read"
        fi
    fi
    if [[ -n "${problem}" ]]; then
        log "ERROR" "Cannot use the backup key file ${key}: ${problem}."
        log "ERROR" "It must be a regular file owned by the user running this script, with no"
        log "ERROR" "permission for the group or others (mode 0600, say), holding the passphrase"
        log "ERROR" "as its first line (docs/backup_runbook.md, section 3.3)."
        if [[ -n "${instead}" ]]; then
            log "ERROR" "${instead}"
        fi
        exit 1
    fi
}

# An explicit choice of an UNENCRYPTED artifact (backup.sh --no-encrypt,
# restore.sh --plaintext-safety-dump) is said once: a WARNING when a key file
# is there to be used, an INFO line otherwise.
note_plaintext_choice() {
    local flag="$1" artifact="$2"
    if [[ -e "${SHEKEL_BACKUP_KEY_FILE}" || -L "${SHEKEL_BACKUP_KEY_FILE}" ]]; then
        log "WARNING" "${flag}: writing ${artifact} UNENCRYPTED although the key file ${SHEKEL_BACKUP_KEY_FILE} exists"
    else
        log "INFO" "${flag}: writing ${artifact} unencrypted"
    fi
}

# ── Backup artifact handling ─────────────────────────────────────
# Set the variable named $1 to the name the artifact $2 (a path with a
# directory part) is written under until it is complete: a dotfile beside
# it, so the rename into place stays on one filesystem, and no glob over the
# artifacts' names (retention's shekel_backup_*, the verify cron line's) and
# no plain ls can select a partial one.
partial_path_for() {
    printf -v "$1" '%s/.%s.partial' "${2%/*}" "${2##*/}"
}

# Write a compressed dump of the database to the artifact $1, encrypted with
# the backup key exactly when $1 ends in .gpg; the caller has checked the key
# (require_backup_key).  Reads DB_CONTAINER, PGUSER and PGDATABASE from the
# caller.  pg_dump flags: --clean/--if-exists put DROP ... IF EXISTS before
# each CREATE; --no-owner/--no-privileges keep the dump portable; --schema
# limits it to SHEKEL_DUMP_SCHEMAS, so a schema added there is dumped by
# every caller (OPS/SH-06).
#
# ONE pipeline streams into the partial file, renamed into place only after
# EVERY stage exited 0 (pipefail is set here, whatever the caller's options,
# and each stage's status is logged on a failure): an encrypted artifact
# never has a plaintext copy on
# disk, and a pg_dump that dies after writing part of the database -- gzip
# and gpg still exit 0 on the shortened stream, and the .gpg still decrypts
# -- leaves nothing behind.  Files are created 0600 and any directory it
# creates 0700 (finding BAL-663: the artifact is the whole database); an
# existing directory's mode is not changed.
#
# On failure it removes the partial file, logs each stage's exit status and
# then $2 (what the failure means for the caller), and EXITS the script.
write_dump_artifact() {
    local path="$1" advice="$2" partial caller_umask schema
    local -a schema_flags=() dump seal stages
    local -
    set -o pipefail
    for schema in "${SHEKEL_DUMP_SCHEMAS[@]}"; do
        schema_flags+=("--schema=${schema}")
    done
    dump=(docker exec "${DB_CONTAINER}" pg_dump -U "${PGUSER}" -d "${PGDATABASE}"
        --clean --if-exists --no-owner --no-privileges "${schema_flags[@]}")
    seal=(cat)
    if [[ "${path}" == *.gpg ]]; then
        seal=(gpg --batch --yes --quiet --passphrase-file "${SHEKEL_BACKUP_KEY_FILE}"
            --symmetric --cipher-algo AES256)
    fi
    partial_path_for partial "${path}"
    caller_umask=$(umask)
    umask 077
    mkdir -p -- "${path%/*}/"
    # A partial file a killed run left would keep its old mode through the
    # truncating redirect below.
    rm -f -- "${partial}"
    if ! "${dump[@]}" | gzip | "${seal[@]}" >"${partial}"; then
        stages=("${PIPESTATUS[@]}")
        rm -f -- "${partial}"
        log "ERROR" "Could not write ${path}: pg_dump exited ${stages[0]}, gzip ${stages[1]}, ${seal[0]} ${stages[2]}; nothing was written."
        log "ERROR" "${advice}"
        exit 1
    fi
    mv -- "${partial}" "${path}"
    umask "${caller_umask}"
}

# Stream a backup artifact's decrypted, decompressed SQL to stdout.  A .gpg
# artifact is decrypted with the backup key file, which the caller has
# checked once (require_backup_key).
backup_stream() {
    local file="$1"
    if [[ "${file}" == *.gpg ]]; then
        gpg --batch --quiet --passphrase-file "${SHEKEL_BACKUP_KEY_FILE}" -d "${file}" \
            | gunzip
    else
        gunzip -c "${file}"
    fi
}

# Fully validate a backup artifact WITHOUT touching any database: the
# key file must decrypt it (for .gpg), the gzip stream must pass an
# end-to-end integrity decode, and the decoded SQL must be non-empty.
# This is the pre-flight that must pass BEFORE any destructive restore
# step (audit OPS/SH-01: restore previously dropped the production
# database before reading a single byte of the artifact, so a corrupt
# file or wrong passphrase left an empty database and no way back).
validate_backup_artifact() {
    local file="$1"
    local decoded_bytes

    if [[ ! -s "${file}" ]]; then
        log "ERROR" "Backup artifact is missing or empty: ${file}"
        return 1
    fi
    # Subshell-local pipefail: the gpg/gunzip stage failures must reach the
    # if-condition even when a caller runs this helper without pipefail.
    if ! decoded_bytes=$(
        set -o pipefail
        backup_stream "${file}" | wc -c
    ); then
        log "ERROR" "Backup artifact failed validation (decryption or gzip integrity): ${file}"
        log "ERROR" "Check the backup key file ${SHEKEL_BACKUP_KEY_FILE} (for .gpg) and the file's integrity."
        return 1
    fi
    if [[ "${decoded_bytes}" -eq 0 ]]; then
        log "ERROR" "Backup artifact decodes to ZERO bytes of SQL: ${file}"
        return 1
    fi
    log "INFO" "Backup artifact validated: ${file} (${decoded_bytes} bytes of SQL)"
    return 0
}
