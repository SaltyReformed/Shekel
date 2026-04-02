#!/usr/bin/env bash
# Post-edit hook: run Pylint on edited Python files
# Claude Code passes the file path as $1 via $TOOL_INPUT_PATH

FILE="$1"

# Only lint Python files in the app directory
if [[ "$FILE" == app/*.py ]]; then
    pylint "$FILE" --fail-on=E,F --disable=C,R 2>&1
    exit $?
fi

# Non-Python files or files outside app/ -- pass through
exit 0
