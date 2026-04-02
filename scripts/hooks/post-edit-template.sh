#!/usr/bin/env bash
# PostToolUse: Check Jinja templates for high-risk patterns.
# Targets audit findings H-01 (string comparisons) and H-05 (float arithmetic).

FILE="$1"
WARNINGS=""

# Only check HTML template files
[[ "$FILE" != app/templates/*.html ]] && exit 0

# --- Check 1: string comparisons on ref table names (caused H-01) ---
# Matches patterns like .name == "something" or .name != 'something'
STRING_CMP=$(grep -nP '\.(name|type_name)\s*(==|!=)\s*["\x27]' "$FILE" 2>/dev/null)
if [ -n "$STRING_CMP" ]; then
    WARNINGS+="REF TABLE STRING COMPARISON: Use IDs, not .name, for logic.\n"
    WARNINGS+="$STRING_CMP\n\n"
fi

# --- Check 2: float filter on financial amounts (caused H-05) ---
FLOAT_FILTER=$(grep -n "|float" "$FILE" 2>/dev/null)
if [ -n "$FLOAT_FILTER" ]; then
    WARNINGS+="FLOAT IN TEMPLATE: Do not use |float for financial calculations.\n"
    WARNINGS+="Compute in the route using Decimal. Pass pre-computed values.\n"
    WARNINGS+="$FLOAT_FILTER\n\n"
fi

# --- Check 3: |safe filter (XSS risk) ---
SAFE_FILTER=$(grep -n "|safe" "$FILE" 2>/dev/null)
if [ -n "$SAFE_FILTER" ]; then
    WARNINGS+="|safe FILTER: Verify this is NOT applied to user-provided data.\n"
    WARNINGS+="$SAFE_FILTER\n\n"
fi

# --- Check 4: state-changing hx-get (should be hx-post) ---
HX_GET_STATE=$(grep -nP 'hx-get=.*delet|hx-get=.*remov|hx-get=.*creat|hx-get=.*updat' "$FILE" 2>/dev/null)
if [ -n "$HX_GET_STATE" ]; then
    WARNINGS+="STATE-CHANGING GET: Use hx-post for mutations, not hx-get.\n"
    WARNINGS+="$HX_GET_STATE\n\n"
fi

if [ -n "$WARNINGS" ]; then
    echo "=== Template warnings for $FILE ==="
    echo -e "$WARNINGS"
    exit 1
fi

exit 0
