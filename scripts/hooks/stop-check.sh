#!/usr/bin/env bash
# Stop hook: verify work quality before Claude reports done.

WARNINGS=""

# --- Check 1: TODO/FIXME/HACK markers in app code ---
TODOS=$(grep -rn "TODO\|FIXME\|HACK\|XXX" app/ --include="*.py" 2>/dev/null | head -10)
if [ -n "$TODOS" ]; then
    WARNINGS+="INCOMPLETE MARKERS found in app/:\n$TODOS\n\n"
fi

# --- Check 2: New pylint: disable comments in this session ---
NEW_DISABLES=$(git diff HEAD -- '*.py' 2>/dev/null | grep "^+" | grep "pylint: disable" | head -5)
if [ -n "$NEW_DISABLES" ]; then
    WARNINGS+="NEW PYLINT SUPPRESSION detected:\n$NEW_DISABLES\n\n"
fi

# --- Check 3: New except Exception blocks in this session ---
NEW_BROAD=$(git diff HEAD -- '*.py' 2>/dev/null | grep "^+" | grep "except Exception" | head -5)
if [ -n "$NEW_BROAD" ]; then
    WARNINGS+="NEW BROAD EXCEPT detected:\n$NEW_BROAD\n\n"
fi

# --- Check 4: Uncommitted changes reminder ---
CHANGED=$(git status --porcelain 2>/dev/null | head -10)
if [ -n "$CHANGED" ]; then
    COUNT=$(git status --porcelain 2>/dev/null | wc -l)
    WARNINGS+="UNCOMMITTED CHANGES ($COUNT files):\n$CHANGED\n\n"
fi

# --- Check 5: Branch check ---
BRANCH=$(git branch --show-current 2>/dev/null)
if [ -n "$BRANCH" ] && [ "$BRANCH" != "dev" ]; then
    WARNINGS+="WRONG BRANCH: Currently on '$BRANCH', expected 'dev'.\n\n"
fi

if [ -n "$WARNINGS" ]; then
    echo "=== End-of-turn checks ==="
    echo -e "$WARNINGS"
fi

# Stop hooks are informational -- always exit 0
exit 0
