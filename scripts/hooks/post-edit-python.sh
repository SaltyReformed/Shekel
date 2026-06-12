#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): enforce Python standards on the edited file.
#
# Reads the edited path from the stdin JSON payload (see _hooklib.sh) and lints
# it against the FULL project config so design smells (too-many-*, line-too-long,
# missing-docstring) and the project's custom checkers are caught in-loop -- the
# old hook disabled C and R, blinding it to exactly those classes.
#
# Two-tier response:
#   * Hard block (exit 2, fed back to Claude) on real errors (E/F) and the custom
#     checkers: the financial-correctness rules (shekel-decimal-from-float,
#     shekel-refname-compare, shekel-bare-money-quantize) and
#     shekel-disable-rationale (every disable must carry a standard Pylint:
#     why-comment). These have zero violations in the current tree, so this never
#     false-blocks correct code; it catches a regression the instant it is typed.
#   * Smell tier: once the 10.00/10 lock-in sentinel
#     (scripts/hooks/ENFORCE_PYLINT_FLOOR) exists -- it has since 2026-06-09 --
#     remaining smells/conventions ALSO hard-block. app/ and scripts/ are at
#     10.00/10, so any single-file message is by definition a fresh regression,
#     and PostToolUse stdout on exit 0 is transcript-only (invisible to the
#     model in normal operation), so advisory output was reaching no one
#     (polyglot audit 2026-06-12, HOOK/SH-06). Without the sentinel the tier
#     stays advisory, preserving the original burn-down semantics.
#
# duplicate-code (R0801) is a whole-package check and CANNOT surface from a
# single-file lint; the Stop hook is what catches a re-introduced DRY cluster.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)" || exit 2

case "$FILE" in
    app/*.py | scripts/*.py | tests/*.py) ;;
    *) exit 0 ;;
esac

cd "$(hook_repo_root)" || {
    echo "post-edit-python: cannot cd to project root '$(hook_repo_root)' -- failing closed." >&2
    exit 2
}

case "$FILE" in
    tests/*.py)
        # tests/ are out of general pylint scope (ratified decision #1), but a
        # Decimal built from a float in a hand-computed assertion is a real bug,
        # so the monetary-precision checker still applies here. unknown/bad-option-value
        # are disabled explicitly (--disable=all does not cover them) so a test
        # file's "disable=X -- explanation" inline comment does not derail the scan.
        guard="$(pylint "$FILE" --score=no --disable=all \
            --enable=shekel-decimal-from-float \
            --disable=unknown-option-value,bad-option-value 2>&1)"
        [ -n "$guard" ] || exit 0
        {
            echo "Monetary-precision violation in test file $FILE -- fix before continuing:"
            echo "$guard"
        } >&2
        exit 2
        ;;
esac

# Hard-block tier: errors and the custom financial-correctness rules.
guard="$(pylint "$FILE" --score=no --disable=all \
    --enable=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale 2>&1)"
if [ -n "$guard" ]; then
    {
        echo "Blocking issue in $FILE (error or financial-correctness rule)."
        echo "Fix the root cause; do not silence it with a bare disable:"
        echo "$guard"
    } >&2
    exit 2
fi

# Smell tier: full project config on the single file. Hard-blocks once the
# 10.00/10 floor is locked in (sentinel present); advisory before that.
smells="$(pylint "$FILE" --score=no 2>&1)"
if [ -n "$smells" ]; then
    if [ -f "scripts/hooks/ENFORCE_PYLINT_FLOOR" ]; then
        {
            echo "Pylint regression in $FILE (the 10.00/10 floor is locked in)."
            echo "Fix at the root; do not silence with a bare disable:"
            echo "$smells"
        } >&2
        exit 2
    fi
    echo "Pylint notes for $FILE (advisory until the floor sentinel exists):"
    echo "$smells"
fi
exit 0
