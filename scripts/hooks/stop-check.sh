#!/usr/bin/env bash
# Stop hook: end-of-turn quality gate.
#
# Informational checks (always exit 0): incomplete markers, newly-added pylint
# suppressions / broad excepts, uncommitted changes, branch.
#
# Pylint floor (the hard gate): when uncommitted app/ Python changes exist, run
# the full `pylint app/`. This is the ONLY place a re-introduced cross-file
# duplicate-code (R0801) cluster is caught -- a single-file per-edit lint cannot
# see it. Enforcement is DEFERRED behind the sentinel
# scripts/hooks/ENFORCE_PYLINT_FLOOR: while app/ is still being driven to 10/10
# (Phase 3/4 of docs/audits/pylint-cleanup/plan.md), a dirty run only WARNS, so
# this hook does not block on the ~239 pre-existing messages. At the lock-in step,
# create the sentinel (`touch scripts/hooks/ENFORCE_PYLINT_FLOOR`) and a dirty
# run HARD-BLOCKS (exit 2): from then on Claude cannot finish a turn that leaves
# app/ regressed below 10.00/10.

set -uo pipefail

REPO="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$REPO" || exit 0

WARNINGS=""

# --- Check 1: incomplete markers in app code ---
TODOS=$(grep -rn "TODO\|FIXME\|HACK\|XXX" app/ --include="*.py" 2>/dev/null | head -10)
if [ -n "$TODOS" ]; then
    WARNINGS+="INCOMPLETE MARKERS in app/:\n$TODOS\n\n"
fi

# --- Check 2: pylint suppressions added this session ---
NEW_DISABLES=$(git diff HEAD -- '*.py' 2>/dev/null | grep "^+" | grep "pylint: disable" | head -5)
if [ -n "$NEW_DISABLES" ]; then
    WARNINGS+="NEW PYLINT SUPPRESSION (must be scoped + symbol-named + why-commented):\n$NEW_DISABLES\n\n"
fi

# --- Check 3: broad except blocks added this session ---
NEW_BROAD=$(git diff HEAD -- '*.py' 2>/dev/null | grep "^+" | grep "except Exception" | head -5)
if [ -n "$NEW_BROAD" ]; then
    WARNINGS+="NEW BROAD EXCEPT (catch specific exceptions):\n$NEW_BROAD\n\n"
fi

# --- Pylint floor: full app/ run when app/ Python changed (catches R0801) ---
APP_PY_CHANGED=$(git status --porcelain -- app 2>/dev/null | grep -E '\.py$')
if [ -n "$APP_PY_CHANGED" ]; then
    LINT=$(pylint app/ --score=no 2>&1)
    if [ -n "$LINT" ]; then
        if [ -f "scripts/hooks/ENFORCE_PYLINT_FLOOR" ]; then
            {
                echo "pylint app/ is not clean and the 10.00/10 floor is enforced."
                echo "Fix the regression before finishing (duplicate-code, design smells,"
                echo "conventions). Do not silence with a bare disable:"
                echo "$LINT"
            } >&2
            exit 2
        fi
        WARNINGS+="PYLINT app/ NOT CLEAN (floor not yet enforced -- see plan Phase 5):\n$LINT\n\n"
    fi
fi

# --- Check 4: uncommitted changes reminder ---
CHANGED=$(git status --porcelain 2>/dev/null | head -10)
if [ -n "$CHANGED" ]; then
    COUNT=$(git status --porcelain 2>/dev/null | wc -l)
    WARNINGS+="UNCOMMITTED CHANGES ($COUNT files):\n$CHANGED\n\n"
fi

# --- Check 5: branch reminder (feature branches are expected during dev) ---
BRANCH=$(git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "main" ]; then
    WARNINGS+="ON main: main is branch-protected; develop on dev or a feature branch.\n\n"
fi

if [ -n "$WARNINGS" ]; then
    echo "=== End-of-turn checks ==="
    echo -e "$WARNINGS"
fi
exit 0
