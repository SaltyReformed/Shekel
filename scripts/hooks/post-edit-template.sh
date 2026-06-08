#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): check edited Jinja templates for high-risk
# patterns behind audit findings H-01 (ref-name string comparisons), H-05 (float
# arithmetic in templates), XSS (|safe), and state-changing GETs. pylint cannot
# parse Jinja, so these stay as targeted greps. Reads the edited path from the
# stdin JSON payload (see _hooklib.sh). Exit 2 blocks and feeds the findings back
# to Claude to fix.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)"
[ -z "$FILE" ] && exit 0
[[ "$FILE" == app/templates/*.html ]] || exit 0

WARNINGS=""

# --- Check 1: string comparisons on ref-table names (caused H-01) ---
STRING_CMP=$(grep -nP '\.(name|type_name)\s*(==|!=)\s*["\x27]' "$FILE" 2>/dev/null)
if [ -n "$STRING_CMP" ]; then
    WARNINGS+="REF TABLE STRING COMPARISON: use IDs, not .name, for logic.\n"
    WARNINGS+="$STRING_CMP\n\n"
fi

# --- Check 2: float filter on financial amounts (caused H-05) ---
FLOAT_FILTER=$(grep -n "|float" "$FILE" 2>/dev/null)
if [ -n "$FLOAT_FILTER" ]; then
    WARNINGS+="FLOAT IN TEMPLATE: do not use |float for financial values.\n"
    WARNINGS+="Compute in the route using Decimal; pass pre-computed values.\n"
    WARNINGS+="$FLOAT_FILTER\n\n"
fi

# --- Check 3: |safe filter (XSS risk) ---
SAFE_FILTER=$(grep -n "|safe" "$FILE" 2>/dev/null)
if [ -n "$SAFE_FILTER" ]; then
    WARNINGS+="|safe FILTER: verify this is NOT applied to user-provided data.\n"
    WARNINGS+="$SAFE_FILTER\n\n"
fi

# --- Check 4: state-changing hx-get (should be hx-post) ---
HX_GET_STATE=$(grep -nP 'hx-get=.*(delet|remov|creat|updat)' "$FILE" 2>/dev/null)
if [ -n "$HX_GET_STATE" ]; then
    WARNINGS+="STATE-CHANGING GET: use hx-post for mutations, not hx-get.\n"
    WARNINGS+="$HX_GET_STATE\n\n"
fi

if [ -n "$WARNINGS" ]; then
    {
        echo "Template issues in $FILE -- fix before continuing:"
        echo -e "$WARNINGS"
    } >&2
    exit 2
fi
exit 0
