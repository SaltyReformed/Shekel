#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): check edited Jinja templates for high-risk
# patterns behind audit findings H-01 (ref-name string comparisons), H-05 (float
# arithmetic in templates), XSS (|safe), and state-changing GETs. pylint cannot
# parse Jinja, so these stay as targeted greps. Reads the edited path from the
# stdin JSON payload (see _hooklib.sh). Exit 2 blocks and feeds the findings back
# to Claude to fix.
#
# Polyglot audit 2026-06-12 hardening (HOOK/SH-04, HOOK/TPL-01, HOOK/TPL-02,
# HOOK/SH-13 in docs/audits/polyglot-cleanup/findings.md): the hook now fails
# CLOSED when it cannot read its target (previously a wrong-cwd invocation made
# every grep miss with stderr discarded and the hook passed clean -- verified by
# fixture); the patterns cover the spaced-pipe ({{ x | safe }}), reversed
# ('done' == x.name), and membership (x.name in [...]) variants that the
# original left-anchored greps missed (verified by fixture); the hx-get verb
# list carries this app's actual mutation vocabulary; and findings are emitted
# without echo -e so backslashes in matched source lines are not reinterpreted.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)" || exit 2
[[ "$FILE" == app/templates/*.html ]] || exit 0

cd "$(hook_repo_root)" || {
    echo "post-edit-template: cannot cd to project root '$(hook_repo_root)' -- failing closed." >&2
    exit 2
}
if [ ! -r "$FILE" ]; then
    echo "post-edit-template: edited template '$FILE' is not readable from $PWD -- failing closed." >&2
    exit 2
fi

WARNINGS=""

# --- Check 1: string comparisons on ref-table names (caused H-01) ---
# Three shapes: forward (x.name == 'lit'), reversed ('lit' == x.name), and
# membership (x.name in ['a','b'] / not in). IDs drive logic; names are display.
STRING_CMP=$(grep -nP '\.(name|type_name)\s*(==|!=)\s*["\x27]' "$FILE")
REVERSED_CMP=$(grep -nP '["\x27]\s*(==|!=)\s*[\w.]+\.(name|type_name)\b' "$FILE")
MEMBER_CMP=$(grep -nP '\.(name|type_name)\s+(not\s+)?in\s*[\[(]' "$FILE")
if [ -n "$STRING_CMP$REVERSED_CMP$MEMBER_CMP" ]; then
    WARNINGS+="REF TABLE STRING COMPARISON: use IDs, not .name, for logic."$'\n'
    [ -n "$STRING_CMP" ] && WARNINGS+="$STRING_CMP"$'\n'
    [ -n "$REVERSED_CMP" ] && WARNINGS+="$REVERSED_CMP"$'\n'
    [ -n "$MEMBER_CMP" ] && WARNINGS+="$MEMBER_CMP"$'\n'
    WARNINGS+=$'\n'
fi

# --- Check 2: float filter on financial amounts (caused H-05) ---
FLOAT_FILTER=$(grep -nP '\|\s*float\b' "$FILE")
if [ -n "$FLOAT_FILTER" ]; then
    WARNINGS+="FLOAT IN TEMPLATE: do not use |float for financial values."$'\n'
    WARNINGS+="Compute in the route using Decimal; pass pre-computed values."$'\n'
    WARNINGS+="$FLOAT_FILTER"$'\n\n'
fi

# --- Check 3: |safe filter (XSS risk) ---
SAFE_FILTER=$(grep -nP '\|\s*safe\b' "$FILE")
if [ -n "$SAFE_FILTER" ]; then
    WARNINGS+="|safe FILTER: verify this is NOT applied to user-provided data."$'\n'
    WARNINGS+="$SAFE_FILTER"$'\n\n'
fi

# --- Check 4: state-changing hx-get (should be hx-post) ---
# Heuristic verb stems include this app's actual mutation vocabulary (status
# workflow projected->done|credit|cancelled, done->settled; UI verbs mark/
# settle/cancel/toggle/approve; move_/mark_ underscore forms). 'pay' is
# deliberately absent: it matches read-only URLs like /payments.
HX_GET_STATE=$(grep -nP 'hx-get=.*(delet|remov|creat|updat|mark[-_]|settle|cancel|toggle|approve|move[-_])' "$FILE")
if [ -n "$HX_GET_STATE" ]; then
    WARNINGS+="STATE-CHANGING GET: use hx-post for mutations, not hx-get."$'\n'
    WARNINGS+="$HX_GET_STATE"$'\n\n'
fi

if [ -n "$WARNINGS" ]; then
    {
        echo "Template issues in $FILE -- fix before continuing:"
        printf '%s\n' "$WARNINGS"
    } >&2
    exit 2
fi
exit 0
