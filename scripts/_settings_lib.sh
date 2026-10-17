#!/bin/bash
# Shekel Budget App -- The settings the shell scripts read from their environment.
# SOURCE this file ("source _settings_lib.sh"); do not execute it.
#
# Each setting's default has ONE home, the table SHEKEL_SETTING_DEFAULTS
# below: one entry per setting NAME, whichever scripts read it, so a name
# means one thing, with one default, everywhere (plan step balance:X-do,
# findings BAL-660 and BAL-664).  A script names the settings it reads ONCE,
# with read_settings, which sets each from the environment or else from the
# table; its --help prints that same list with print_settings.  So a --help
# lists exactly the settings the script declared, each with the default the
# script uses, and no default is spelled in a script.  A setting the script
# reads WITHOUT declaring it is not made impossible here: under set -u it
# fails where it is read, when the environment lacks it.  Sourcing this file
# again keeps both arrays and the entries a caller added; the table's own
# entries are set to their defaults again.

# The production containers' names have their one home in _docker_lib.sh.  A
# copy of it lacking either function is refused here, before a default is
# filled from a "command not found" (finding BAL-625).
_settings_dir=$(dirname "${BASH_SOURCE[0]}")
_docker_lib="${_settings_dir}/_docker_lib.sh"
# shellcheck source=_docker_lib.sh
if ! {
    source "${_docker_lib}" \
        && declare -F shekel_prod_app_container shekel_prod_db_container >/dev/null
}; then
    printf '%s: cannot load %s, or it lacks a function the settings read\n' "$0" "${_docker_lib}" >&2
    printf '(shekel_prod_app_container, shekel_prod_db_container).  It must come from the\n' >&2
    printf 'same revision as this file.\n' >&2
    exit 1
fi

# The checkout this file is in: the one scripts/deploy.sh deploys and whose
# committed verifier key it reads.  CDPATH is cleared for the cd, or a
# relative path found through it would be printed as a second line.
SHEKEL_REPO_ROOT=$(CDPATH='' cd -- "${_settings_dir}/.." && pwd)

# Where a default holds this, read_settings puts the run's process id
# (a name that must differ between two runs at once); --help prints the
# placeholder itself, never one run's number.
SHEKEL_SETTING_PID_PLACEHOLDER='<pid>'

# The two arrays are created only when the name is not already this
# process's array of that kind, so a second source keeps them.  Anything else
# the name holds is unset first: a scalar inherited from the environment, or
# an array of the other kind, which declare cannot convert.  The kind is read
# from `declare -p`, which prints `declare -A` (or `-Ax`, exported) for an
# associative array and `declare -a` for an indexed one, empty or not;
# `${NAME@a}` cannot be used: under set -u bash refuses it for an unset name
# and for an EMPTY array (bash 5.3.20, 2026-10-17).
_settings_declared=$(declare -p SHEKEL_SETTING_DEFAULTS 2>/dev/null) || true
if [[ "${_settings_declared}" != "declare -A"* ]]; then
    unset SHEKEL_SETTING_DEFAULTS
    # Setting name -> default.
    declare -gA SHEKEL_SETTING_DEFAULTS=()
fi
_settings_declared=$(declare -p SHEKEL_SETTINGS_READ 2>/dev/null) || true
if [[ "${_settings_declared}" != "declare -a"* ]]; then
    unset SHEKEL_SETTINGS_READ
    # What read_settings read, in order: name, meaning, name, meaning ...
    declare -ga SHEKEL_SETTINGS_READ=()
fi

SHEKEL_SETTING_DEFAULTS+=(
    [BACKUP_LOCAL_DIR]=/var/backups/shekel
    [BACKUP_NAS_DIR]=/mnt/nas/backups/shekel
    [PGUSER]=shekel_user
    [PGDATABASE]=shekel
    # One database per run, so two verifications at once never drop each
    # other's (OPS/SH-27).
    [VERIFY_DB]="shekel_verify_${SHEKEL_SETTING_PID_PLACEHOLDER}"
    [RETENTION_DAILY_DAYS]=7
    [RETENTION_WEEKLY_WEEKS]=4
    [RETENTION_MONTHLY_MONTHS]=6
    [RETENTION_MIN_KEEP]=3
    [RETENTION_STALE_ALERT_DAYS]=2
    [HEALTH_TIMEOUT]=60
    [HEALTH_INTERVAL]=5
    # The verifier key committed in this checkout.
    [COSIGN_PUBLIC_KEY]="${SHEKEL_REPO_ROOT}/deploy/cosign.pub"
    [COSIGN_PRIVATE_KEY]=/etc/shekel/cosign.key
    [COSIGN_REQUIRED]=false
)
# The production containers' names, from their one home, one per statement:
# a second command substitution in one assignment would hide the first one's
# exit status.
SHEKEL_SETTING_DEFAULTS[DB_CONTAINER]="$(shekel_prod_db_container)"
SHEKEL_SETTING_DEFAULTS[APP_CONTAINER]="$(shekel_prod_app_container)"

# read_settings NAME MEANING [NAME MEANING ...]
# Set each NAME from its environment value or, when that is unset or empty,
# from its default, and record it with MEANING for print_settings.  A NAME
# with no default (named in the refusal), or an odd argument count (a NAME
# without a MEANING; the count is named), is a mistake in the calling
# script: it EXITS the script, whatever context calls it.  An even number
# of missing meanings cannot be told from pairs: a name then reads as the
# meaning before it.
read_settings() {
    local name default
    if (($# % 2 != 0)); then
        printf '%s: read_settings takes NAME MEANING pairs; it got %d arguments\n' "$0" "$#" >&2
        exit 1
    fi
    while (($# > 0)); do
        name="$1"
        if [[ -z "${SHEKEL_SETTING_DEFAULTS[${name}]+x}" ]]; then
            printf '%s: the setting %s has no default in SHEKEL_SETTING_DEFAULTS\n' "$0" "${name}" >&2
            exit 1
        fi
        default="${SHEKEL_SETTING_DEFAULTS[${name}]//"${SHEKEL_SETTING_PID_PLACEHOLDER}"/$$}"
        printf -v "${name}" '%s' "${!name:-${default}}"
        SHEKEL_SETTINGS_READ+=("${name}" "$2")
        shift 2
    done
}

# Print the settings section of a --help: each setting read_settings read,
# its meaning, and its default as the table holds it.
print_settings() {
    local i
    echo "Environment variables (each read from the environment, else its default):"
    for ((i = 0; i < ${#SHEKEL_SETTINGS_READ[@]}; i += 2)); do
        printf '    %-28s %s\n' "${SHEKEL_SETTINGS_READ[i]}" "${SHEKEL_SETTINGS_READ[i + 1]}"
        printf '    %-28s default: %s\n' "" "${SHEKEL_SETTING_DEFAULTS[${SHEKEL_SETTINGS_READ[i]}]}"
    done
}
