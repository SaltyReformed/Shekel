#!/bin/bash
# Shekel Budget App -- The settings a shell script reads from its environment.
# SOURCE this file ("source _settings_lib.sh"); do not execute it.
#
# Each setting's default has ONE home, the table SHEKEL_SETTING_DEFAULTS,
# which a family's lib fills (scripts/_backup_lib.sh for the backup scripts).
# A script names the settings it reads ONCE, with read_settings, which sets
# each from the environment or else from the table; its --help prints that
# same list with print_settings.  So a --help lists exactly the settings the
# script declared, each with the default the script uses, and no default is
# spelled in two scripts (plan step balance:X-do, finding BAL-660).  A
# setting the script reads WITHOUT declaring it is not made impossible here:
# under set -u it fails where it is read, when the environment lacks it.
# This file loads nothing, so any script can source it without a family's
# lib.  Source it ONCE per process: a second source empties the table and
# the record.

# Where a default holds this, read_settings puts the run's process id
# (a name that must differ between two runs at once); --help prints the
# placeholder itself, never one run's number.
SHEKEL_SETTING_PID_PLACEHOLDER='<pid>'

# Setting name -> default.  Filled by a family's lib with `+=([NAME]=...)`.
declare -gA SHEKEL_SETTING_DEFAULTS=()

# What read_settings read, in order: name, meaning, name, meaning ...
declare -ga SHEKEL_SETTINGS_READ=()

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
