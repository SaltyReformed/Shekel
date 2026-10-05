#!/usr/bin/env bash
# PreToolUse (Bash): refuse a raw `gh` write to the plan tracker and point the
# session at the plan tool. The decision is guard_tracker_writes.py's; its
# header holds the rules and the rulings behind them.
#
# NOT REGISTERED until the tracker cutover (plan step L8): registration in
# .claude/settings.json IS the switch, and the switched-off state is pinned by
# tests/test_hooks/test_tracker_hooks_switched_off.py.
#
# The matcher will be "Bash", so this runs on EVERY Bash call in every session.
# The pre-filter keeps a payload that does not contain "gh" anywhere to one
# shell `case` test with no python spawn. It tests for "gh" alone: which gh
# groups and verbs matter is the python decision's one home, never a second
# list here. A command that spells gh another way (`g\h`, "$GH", an alias) is
# not read at all -- the header of _gh_commands.py lists what is not.

set -uo pipefail

payload="$(cat)"
case "$payload" in
    *gh*) ;;
    *) exit 0 ;;
esac

# The venv's python (the tracker's name is read from tools/plan/setup_tracker.py,
# whose imports need the pinned requirements), and the fail-closed runner.
source "$(dirname "${BASH_SOURCE[0]}")/_hooklib.sh"
hook_run_guard guard-tracker-writes "$(dirname "${BASH_SOURCE[0]}")/guard_tracker_writes.py" \
    "$payload"
