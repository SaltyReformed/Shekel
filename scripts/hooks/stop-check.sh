#!/usr/bin/env bash
# Stop hook: end-of-turn quality gate.
#
# Informational checks (always exit 0): incomplete markers, newly-added pylint
# suppressions / broad excepts, uncommitted changes, branch. HONEST CHANNEL
# NOTE (polyglot audit 2026-06-12, HOOK/SH-07): Stop-hook stdout on exit 0 is
# transcript-only -- the informational tier is visible in verbose/transcript
# mode, not in the normal loop. It is kept because it costs milliseconds and
# serves transcript review; the LOUD channel in this script is the exit-2
# pylint floor below.
#
# Pylint floor (the hard gate): when app/ or scripts/ Python changed -- whether
# uncommitted (including untracked files in NEW subdirectories: porcelain
# needs -uall, finding HOOK/SH-01) or committed in-turn but not yet pushed
# (finding HOOK/SH-08) -- run the full tree lints. This is the ONLY local
# place a re-introduced cross-file duplicate-code (R0801) cluster is caught --
# a single-file per-edit lint cannot see it. The floors mirror CI exactly
# (finding HOOK/SH-09): pylint app/, pylint scripts/, and the cross-tree
# app/+scripts/ duplicate-code run. Enforcement is gated on the sentinel
# scripts/hooks/ENFORCE_PYLINT_FLOOR (created at the 2026-06-09 lock-in):
# with the sentinel present a dirty run HARD-BLOCKS (exit 2); without it,
# WARN only.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

REPO="$(hook_repo_root)"
cd "$REPO" || {
    # Fail CLOSED (finding HOOK/SH-11): if the gate cannot reach the repo it
    # must say so loudly, not silently wave the turn through.
    echo "stop-check: cannot cd to project root '$REPO' -- failing closed." >&2
    exit 2
}

WARNINGS=""

# --- Check 1: incomplete markers in app code ---
TODOS=$(grep -rn "TODO\|FIXME\|HACK\|XXX" app/ --include="*.py" 2>/dev/null | head -10)
if [ -n "$TODOS" ]; then
    WARNINGS+="INCOMPLETE MARKERS in app/:"$'\n'"$TODOS"$'\n\n'
fi

# Untracked Python files are invisible to `git diff HEAD`; include them in the
# suppression/broad-except scans explicitly (HOOK/SH-07 lesser-included gap).
UNTRACKED_PY=$(git ls-files --others --exclude-standard -- '*.py' 2>/dev/null)

# --- Check 2: pylint suppressions added this session ---
NEW_DISABLES=$(git diff HEAD -- '*.py' 2>/dev/null | grep "^+" | grep "pylint: disable" | head -5)
if [ -n "$UNTRACKED_PY" ]; then
    UNTRACKED_DISABLES=$(printf '%s\n' "$UNTRACKED_PY" | xargs -r -d '\n' grep -n "pylint: disable" 2>/dev/null | head -5)
    [ -n "$UNTRACKED_DISABLES" ] && NEW_DISABLES+=$'\n'"$UNTRACKED_DISABLES"
fi
if [ -n "$NEW_DISABLES" ]; then
    WARNINGS+="NEW PYLINT SUPPRESSION (must be scoped + symbol-named + why-commented):"$'\n'"$NEW_DISABLES"$'\n\n'
fi

# --- Check 3: broad except blocks added this session ---
NEW_BROAD=$(git diff HEAD -- '*.py' 2>/dev/null | grep "^+" | grep "except Exception" | head -5)
if [ -n "$UNTRACKED_PY" ]; then
    UNTRACKED_BROAD=$(printf '%s\n' "$UNTRACKED_PY" | xargs -r -d '\n' grep -n "except Exception" 2>/dev/null | head -5)
    [ -n "$UNTRACKED_BROAD" ] && NEW_BROAD+=$'\n'"$UNTRACKED_BROAD"
fi
if [ -n "$NEW_BROAD" ]; then
    WARNINGS+="NEW BROAD EXCEPT (catch specific exceptions):"$'\n'"$NEW_BROAD"$'\n\n'
fi

# --- Pylint floors: mirror CI when app/ or scripts/ Python changed ---
# Trigger on uncommitted changes (-uall so brand-new subpackage directories
# are seen file-by-file) OR on in-turn commits not yet on the upstream branch.
APP_PY_CHANGED=$(git status --porcelain -uall -- app 2>/dev/null | grep -E '\.py$')
SCRIPTS_PY_CHANGED=$(git status --porcelain -uall -- scripts 2>/dev/null | grep -E '\.py$')
UNPUSHED_PY=$(git log '@{u}..HEAD' --name-only --pretty=format: -- app scripts 2>/dev/null | grep -E '\.py$' | sort -u)
if [ -n "$UNPUSHED_PY" ]; then
    case "$UNPUSHED_PY" in
        *app/*) APP_PY_CHANGED+=$'\n'"unpushed commits touch app/" ;;
    esac
    case "$UNPUSHED_PY" in
        *scripts/*) SCRIPTS_PY_CHANGED+=$'\n'"unpushed commits touch scripts/" ;;
    esac
fi

LINT_FAILURES=""
if [ -n "$APP_PY_CHANGED" ]; then
    LINT=$(pylint app/ --score=no 2>&1)
    [ -n "$LINT" ] && LINT_FAILURES+="--- pylint app/ ---"$'\n'"$LINT"$'\n'
fi
if [ -n "$SCRIPTS_PY_CHANGED" ]; then
    LINT=$(pylint scripts/ --score=no 2>&1)
    [ -n "$LINT" ] && LINT_FAILURES+="--- pylint scripts/ ---"$'\n'"$LINT"$'\n'
fi
if [ -n "$APP_PY_CHANGED" ] || [ -n "$SCRIPTS_PY_CHANGED" ]; then
    # Cross-tree duplicate-code: the gate CI added 2026-06-11 after the
    # seed_user/register_user consolidation; only a combined run can see an
    # app/ <-> scripts/ duplication cluster.
    LINT=$(pylint app/ scripts/ --score=no --disable=all --enable=duplicate-code 2>&1)
    [ -n "$LINT" ] && LINT_FAILURES+="--- cross-tree duplicate-code (app/ + scripts/) ---"$'\n'"$LINT"$'\n'
fi

if [ -n "$LINT_FAILURES" ]; then
    if [ -f "scripts/hooks/ENFORCE_PYLINT_FLOOR" ]; then
        {
            echo "pylint floor violation and the 10.00/10 floor is enforced."
            echo "Fix the regression before finishing (duplicate-code, design smells,"
            echo "conventions). Do not silence with a bare disable:"
            printf '%s\n' "$LINT_FAILURES"
        } >&2
        exit 2
    fi
    WARNINGS+="PYLINT FLOOR NOT CLEAN (floor not yet enforced -- see plan Phase 5):"$'\n'"$LINT_FAILURES"$'\n\n'
fi

# --- Check 4: uncommitted changes reminder ---
CHANGED=$(git status --porcelain 2>/dev/null | head -10)
if [ -n "$CHANGED" ]; then
    COUNT=$(git status --porcelain 2>/dev/null | wc -l)
    WARNINGS+="UNCOMMITTED CHANGES ($COUNT files):"$'\n'"$CHANGED"$'\n\n'
fi

# --- Check 5: branch reminder (feature branches are expected during dev) ---
BRANCH=$(git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "main" ]; then
    WARNINGS+="ON main: main is branch-protected; develop on dev or a feature branch."$'\n\n'
fi

if [ -n "$WARNINGS" ]; then
    echo "=== End-of-turn checks ==="
    printf '%s\n' "$WARNINGS"
fi
exit 0
