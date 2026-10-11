#!/bin/bash
# Shekel Budget App -- the shell scripts' one home of "is this container
# running?".
# SOURCE this file; do not execute it.
#
# Asked by deploy/shekel-deploy.sh before the pre-deploy dump, by
# scripts/_backup_lib.sh (`require_db_container`) before every backup,
# restore and verification, and by scripts/restore.sh while it waits for the
# app container.  Each used to spell it itself, and the developer ruled it
# ONE home (plan step balance:X-dm, ruling R-BAL254).
# (scripts/build_test_db_image.py asks the converse in Python -- has it
# STOPPED, where a failed inspect is no answer -- and cannot load this.)
#
# The answer is captured and compared exactly, with no pipe.  The spelling
# it replaces, `docker inspect ... | grep -q true`, put a reader that exits
# at its first match on a pipe under the callers' pipefail -- the class that
# made shekel-deploy.sh's migration check read a listed revision as absent
# (finding BAL-618).  Here docker writes its one-line answer at once, so
# this site was never seen to fail; it left with the rest of the class.

# 0 when docker reports the named container running; 1 when it reports it
# stopped, cannot find it, or cannot be asked.
container_running() {
    local container="$1" running
    running=$(docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null) || return 1
    [ "${running}" = true ]
}
