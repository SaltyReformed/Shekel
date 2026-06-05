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

# Echo the edited file's path normalized to repo-relative (e.g. app/foo.py).
# Empty output means the payload carried no file_path. Consumes stdin, so call
# it at most once per hook invocation.
hook_target_relpath() {
    local abs root
    abs="$(python3 -c 'import json, sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))
except (ValueError, KeyError):
    pass' 2>/dev/null)"
    [ -z "$abs" ] && return 0
    root="${CLAUDE_PROJECT_DIR:-$PWD}"
    # Strip the project-root prefix so the path guards can match on app/, tests/,
    # etc. A path outside the project is left absolute and simply won't match.
    printf '%s\n' "${abs#"$root/"}"
}
