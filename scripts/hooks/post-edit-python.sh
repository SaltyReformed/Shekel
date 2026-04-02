#!/usr/bin/env bash
# PostToolUse: Check edited Python files for high-risk patterns.
# Targets the patterns behind audit findings C-01, H-01, H-05, and the
# Decimal precision rule.

FILE="$1"
WARNINGS=""

# Only check Python files in app/ or scripts/
[[ "$FILE" != app/*.py && "$FILE" != scripts/*.py ]] && exit 0

# --- Check 1: broad except Exception (caused C-01, the CRITICAL finding) ---
BROAD=$(grep -n "except Exception" "$FILE" 2>/dev/null | grep -v "# pylint: disable")
if [ -n "$BROAD" ]; then
    WARNINGS+="BROAD EXCEPT: 'except Exception' found. Catch specific exceptions.\n"
    WARNINGS+="$BROAD\n\n"
fi

# --- Check 2: Decimal constructed from float, not string ---
# Matches Decimal(0.1) or Decimal(some_number) but not Decimal("0.1") or Decimal('0.1')
BAD_DECIMAL=$(grep -nP 'Decimal\(\s*[0-9]' "$FILE" 2>/dev/null | grep -v 'Decimal("' | grep -v "Decimal('")
if [ -n "$BAD_DECIMAL" ]; then
    WARNINGS+="DECIMAL FROM FLOAT: Construct Decimals from strings, not numbers.\n"
    WARNINGS+="$BAD_DECIMAL\n\n"
fi

# --- Check 3: float() used on monetary values in app code ---
# Skip chart_data_service.py which legitimately converts to float at the Chart.js boundary
if [[ "$FILE" != *"chart_data_service"* ]]; then
    FLOAT_USE=$(grep -n "float(" "$FILE" 2>/dev/null)
    if [ -n "$FLOAT_USE" ]; then
        WARNINGS+="FLOAT USAGE: float() found. Use Decimal for financial values.\n"
        WARNINGS+="$FLOAT_USE\n\n"
    fi
fi

# --- Check 4: pylint ---
LINT=$(pylint "$FILE" --fail-on=E,F --disable=C,R --score=no 2>&1)
LINT_EXIT=$?
if [ $LINT_EXIT -ne 0 ]; then
    WARNINGS+="PYLINT ERRORS:\n$LINT\n\n"
fi

# Report warnings
if [ -n "$WARNINGS" ]; then
    echo "=== Post-edit warnings for $FILE ==="
    echo -e "$WARNINGS"
    exit 1
fi

exit 0
