#!/bin/bash
# Shekel Budget App -- the docker facts the shell scripts share.
# SOURCE this file; do not execute it.
#
# Each fact below used to be spelled by every script that needed it, so a
# change to one was made several times with nothing checking the copies
# agreed (CLAUDE.md rule 14).  The developer ruled the first of them ONE home
# (plan step balance:X-dm, ruling R-BAL254); plan step balance:X-do moved the
# rest here and named the file for what it holds:
#
#   * is this container running?               container_running
#   * is this string an image digest?          image_digest_or_empty
#   * the first image digest in a text         first_image_digest
#   * the production containers' names         shekel_prod_app_container,
#                                               shekel_prod_db_container
#
# Loaded by deploy/shekel-deploy.sh, deploy/verify_prod_deploy.sh,
# scripts/_backup_lib.sh (for backup.sh, restore.sh, verify_backup.sh and
# backup_retention.sh), scripts/deploy.sh, scripts/generate_pg_cert.sh and
# scripts/reconcile_prod_to_canonical.sh.  Each loader names the functions it
# reads with `declare -F` and refuses to start when this file is missing or
# lacks one, so a copy from another revision that lacks a function its loader
# reads is refused before anything is touched, and never reaches a call that
# would fail as "command not found" (finding BAL-625: that status read as
# "the database container is not running").  A copy that has every function
# but answers differently is not detected.  (scripts/build_test_db_image.py
# asks whether a container has STOPPED, in Python, and cannot load this.)

# ── Containers ────────────────────────────────────────────────────
# The production containers' names, as docker-compose.yml's container_name
# sets them.  These are the DEFAULTS only: the override variables stay where
# they were (the scripts under deploy/ read SHEKEL_CONTAINER_NAME and
# SHEKEL_DB_CONTAINER; scripts/deploy.sh and the backup family read
# APP_CONTAINER and DB_CONTAINER; generate_pg_cert.sh and
# reconcile_prod_to_canonical.sh read none), so a
# DB_CONTAINER exported for a backup never reaches shekel-deploy.sh, which
# reads SHEKEL_DB_CONTAINER (.env.example says why).
# docker-compose.yml, the shared nginx's upstream and the host's own scripts
# still spell them for themselves: facts across formats and outside the
# repository are plan step balance:X-ec's subject.
shekel_prod_app_container() { printf '%s\n' shekel-prod-app; }
shekel_prod_db_container() { printf '%s\n' shekel-prod-db; }

# 0 when docker reports the named container running; 1 when it reports it
# stopped, cannot find it, or cannot be asked.
#
# The answer is captured and compared exactly, with no pipe.  The spelling
# it replaced, `docker inspect ... | grep -q true`, put a reader that exits
# at its first match on a pipe under the callers' pipefail -- the class that
# made shekel-deploy.sh's migration check read a listed revision as absent
# (finding BAL-618).  Here docker writes its one-line answer at once, so
# this site was never seen to fail; it left with the rest of the class.
container_running() {
    local container="$1" running
    running=$(docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null) || return 1
    [ "${running}" = true ]
}

# ── Image digests ─────────────────────────────────────────────────
# An image digest: the algorithm and 64 lowercase hex digits, as docker and
# the registry print one.  ONE spelling of its shape (finding BAL-623), an
# extended regular expression so bash's =~ reads it as it stands, built on
# ONE spelling of the algorithm's prefix.  The two functions below read
# them; nothing else should.
#
# Both PRINT their answer, or nothing, and return 0 either way: a caller
# assigns the answer and refuses an empty one, and that assignment and its
# empty check carry the decision.  Errexit is off inside both call forms --
# bash suspends it for a function called in an `if`, and runs a command
# substitution without it unless inherit_errexit is set -- so neither body
# holds a command that can fail.  The assignment form is chosen because a
# predicate in an `if` is the masking shellcheck's SC2310 reports.
IMAGE_DIGEST_PREFIX='sha256:'
IMAGE_DIGEST_ERE="${IMAGE_DIGEST_PREFIX}[0-9a-f]{64}"

# Print $1 when the whole of it is an image digest; print nothing when it is
# not, a bare prefix or a digest with anything around it included.
image_digest_or_empty() {
    if [[ "$1" =~ ^${IMAGE_DIGEST_ERE}$ ]]; then
        printf '%s\n' "$1"
    fi
}

# Print the image digest the text $1 names first, or nothing.  The FIRST
# algorithm prefix in the text decides: the token is that prefix and every
# image-reference character that runs on from it (letters, digits, `.`, `_`
# and `-`), and it is printed only when the whole token is a digest.  Any
# other token answers NOTHING, and no later digest is searched for (plan
# step balance:X-do), so a malformed pin is refused, never cut to 64 digits
# nor replaced by the next one.  A match in bash, not a pipe through
# `grep -o | head`, whose early-exiting reader pipefail could report as a
# failure (plan step balance:X-dm).
first_image_digest() {
    if [[ "$1" =~ ${IMAGE_DIGEST_PREFIX}[[:alnum:]._-]* ]]; then
        image_digest_or_empty "${BASH_REMATCH[0]}"
    fi
}
