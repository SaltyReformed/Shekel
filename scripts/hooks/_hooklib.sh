#!/usr/bin/env bash
# Shared helpers for the Claude Code hooks in this directory.
# SOURCE this file (". _hooklib.sh"); do not execute it.
#
# The harness delivers tool data as a single JSON object on the hook's stdin.
# Verified empirically for this environment (claude_code 2.1.x remote): the
# edited file arrives as tool_input.file_path, an ABSOLUTE path, and there is NO
# TOOL_INPUT_PATH environment variable. The previous "$TOOL_INPUT_PATH" argument
# convention therefore passed an empty string, which made every per-edit hook a
# silent no-op. These helpers parse the payload the documented way.
#
# FAIL-CLOSED CONTRACT (polyglot audit 2026-06-12, findings HOOK/SH-02 and
# HOOK/SH-03 in docs/audits/polyglot-cleanup/findings.md): a gate that cannot
# see its target must not pass it. The original helper returned empty on ANY
# internal failure (malformed payload, python3 missing, payload-shape change)
# and every caller treated empty as "not my file" -- so all four per-edit gates
# failed OPEN on infrastructure error, the same failure mode the header above
# documents already happening once in the TOOL_INPUT_PATH era. Now an
# infrastructure failure returns 1 after stderr diagnostics, and callers exit 2.

# Echo the project root with no trailing slash -- the single authority for
# repo-root resolution across every hook (finding HOOK/SH-12: this was
# previously spelled three different ways across three scripts, and a
# trailing slash in CLAUDE_PROJECT_DIR silently defeated the relpath strip).
hook_repo_root() {
    local root="${CLAUDE_PROJECT_DIR:-$PWD}"
    printf '%s\n' "${root%/}"
}

# Echo the edited file's path normalized to repo-relative (e.g. app/foo.py).
# Consumes stdin, so call it at most once per hook invocation.
#
# Outcomes:
#   * return 0, repo-relative output -- normal case; callers match on app/ etc.
#   * return 0, ABSOLUTE output      -- file lies outside the project; callers'
#     case patterns will not match and the hook skips, which is correct.
#   * return 1 after stderr output   -- infrastructure failure (malformed JSON,
#     python3 unavailable, or a Write|Edit|MultiEdit payload with no
#     tool_input.file_path, which the tool schemas guarantee cannot happen
#     legitimately). Callers MUST exit 2: fail closed, never silently open.
#
# realpath-based normalization makes the result independent of trailing
# slashes, symlinked invocation paths, and the hook's working directory
# (finding HOOK/SH-03: the old exact-prefix strip returned an absolute path --
# and therefore silently skipped every check -- when CLAUDE_PROJECT_DIR carried
# a trailing slash; verified empirically before the fix).
hook_target_relpath() {
    local out root
    root="$(hook_repo_root)"
    if ! out="$(python3 -c '
import json
import os
import sys

root = os.path.realpath(sys.argv[1])
try:
    payload = json.load(sys.stdin)
except ValueError as exc:
    print(f"hook payload is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(1)
path = payload.get("tool_input", {}).get("file_path", "")
if not path:
    print(
        "hook payload carries no tool_input.file_path "
        "(matcher and payload shape disagree -- harness contract change?)",
        file=sys.stderr,
    )
    sys.exit(1)
real = os.path.realpath(path)
rel = os.path.relpath(real, root)
print(path if rel.startswith("..") else rel)
' "$root")"; then
        {
            echo "hook infrastructure error: could not resolve the edited file from the"
            echo "stdin payload (diagnostics above). Failing CLOSED -- the edit is blocked"
            echo "until the hook plumbing is fixed (docs/coding-standards.md: gates fail"
            echo "with a clear message, never with silent defaults)."
        } >&2
        return 1
    fi
    printf '%s\n' "$out"
}
