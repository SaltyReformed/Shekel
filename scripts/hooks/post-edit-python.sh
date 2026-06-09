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
#   * Advisory (exit 0) for the remaining smells/conventions while Phase 3/4 of
#     the cleanup is still in flight. The Stop hook's full `pylint app/` ratchet
#     is the hard gate that forbids a net regression and locks the tree at zero.
#
# duplicate-code (R0801) is a whole-package check and CANNOT surface from a
# single-file lint; the Stop hook is what catches a re-introduced DRY cluster.

set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"

FILE="$(hook_target_relpath)"
[ -z "$FILE" ] && exit 0

case "$FILE" in
    app/*.py | scripts/*.py) ;;
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
    *)
        exit 0
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

# Advisory tier: surface design smells / conventions as in-loop feedback.
smells="$(pylint "$FILE" --score=no 2>&1)"
if [ -n "$smells" ]; then
    echo "Pylint notes for $FILE (advisory now; the Stop-hook ratchet enforces no net regression):"
    echo "$smells"
fi
exit 0
