#!/usr/bin/env bash
# verify_prod_deploy.sh -- read-only checks of production after a deploy.
#
# Usage:
#   verify_prod_deploy.sh [SINCE]    # SINCE: docker logs --since (default 10m)
#
# Exit 0, printing VERIFY OK, only if every check holds; otherwise exit 1,
# printing VERIFY FAILED.  It changes no file, pin or container; its probes
# are anonymous requests, which the app's rate limiter counts like any other.
#   * The app container reports itself healthy.
#   * Anonymous probes from INSIDE it (no port is exposed): /health is 200; a
#     private URL and an unknown URL both 302 to /login (the login gate's
#     default deny, ruling bank_import:R-BI4); /login is 200.
#   * Its log since SINCE was read, and holds no Traceback or ERROR line.
#
# Run it from ~/projects/Shekel once that checkout is fast-forwarded to main,
# the checkout shekel-deploy runs from, with the time shekel-deploy started
# as SINCE (card X-cx L9's /release skill is to do both).
#
# A KNOWN FALSE FAILURE (finding BAL-628, owner X-do): after an in-place
# RESTART of the same container, not after a deploy (which creates a fresh
# one), gunicorn's control server logs an ERROR line, `Control server error:
# [Errno 13] Permission denied`, and this reads VERIFY FAILED.  Read the
# ERROR line before treating a failure after a restart as real.
#
# Written outside the repository for the coordinator's deploys (2026-09-12)
# and moved here by card X-cx L9a, so the check that grades production is
# reviewed and linted like the script whose work it grades.
set -u

CONTAINER="${SHEKEL_CONTAINER_NAME:-shekel-prod-app}"
since="${1:-10m}"
ok=0

status=$(docker ps --filter "name=${CONTAINER}" --format '{{.Status}}')
echo "container ${CONTAINER}: ${status}"
case "$status" in
    *"(healthy)"*) ;;
    *)
        echo "FAIL: container not healthy"
        ok=1
        ;;
esac

probe() {
    docker exec "$CONTAINER" python -c "
import urllib.request, urllib.error, sys
url='http://localhost:8000'+sys.argv[1]
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
op=urllib.request.build_opener(NoRedirect)
try:
    r=op.open(url, timeout=5); print(r.status, r.headers.get('Location',''))
except urllib.error.HTTPError as e: print(e.code, e.headers.get('Location',''))
" "$1"
}

expect() { # expect <path> <status> [location-prefix]
    local out code loc
    out=$(probe "$1")
    code=${out%% *}
    loc=${out#* }
    if [ "$code" = "$2" ] && { [ -z "${3:-}" ] || [[ "$loc" == "$3"* ]]; }; then
        echo "ok   $1 -> $out"
    else
        echo "FAIL $1 -> $out (expected $2 ${3:-})"
        ok=1
    fi
}

expect /health 200
expect /dashboard 302 /login
expect /no-such-url-xyz 302 /login
expect /login 200

# Read first, then counted: piped straight into `grep -c`, a `docker logs`
# that failed counted zero error lines and passed this check.
if logs=$(docker logs --since "$since" "$CONTAINER" 2>&1); then
    bad=$(grep -cE 'Traceback|ERROR' <<<"$logs")
    echo "log lines with Traceback/ERROR in the last ${since}: ${bad}"
    [ "$bad" = 0 ] || ok=1
else
    echo "FAIL: could not read the log of ${CONTAINER}: ${logs}"
    ok=1
fi

if [ "$ok" = 0 ]; then
    echo "VERIFY OK"
else
    echo "VERIFY FAILED"
fi
exit "$ok"
