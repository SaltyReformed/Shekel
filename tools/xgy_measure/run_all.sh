#!/bin/bash
# X-gy MEASUREMENT ONLY (branch measure/x-gy, never merged).
#
# Runs the same slice of the suite under several cluster/worker arms, one
# after another ON ONE RUNNER, so every arm shares the hardware it is compared
# on.  The order is rotated per replicate so a warm page cache or a noisy
# neighbour cannot favour one arm systematically.
#
# usage: run_all.sh <replicate>
set -uo pipefail

REPLICATE="${1:?replicate}"
PG_IMAGE="postgres:18-alpine@sha256:6c538e7206ea40ff740ef27883529390a690b6ead6ba96b44c67a9f7c638e8fd"
mapfile -t SLICE <tools/xgy_measure/slice.txt
HALF_A=()
HALF_B=()
QUARTERS=("" "" "" "")
for i in "${!SLICE[@]}"; do
    if ((i % 2 == 0)); then HALF_A+=("${SLICE[$i]}"); else HALF_B+=("${SLICE[$i]}"); fi
    QUARTERS[i % 4]+="${SLICE[$i]} "
done
COMMON=(-m "not docker" -p no:randomly -q --durations=0)

# Run one arm; print tagged summary lines the log can be grepped for.
arm() {
    local name="$1"
    shift
    local log="/tmp/xgy-arm-${name}.log"
    local t0 t1 rc
    t0=$(date +%s.%N)
    "$@" >"$log" 2>&1
    rc=$?
    t1=$(date +%s.%N)
    echo "::group::ARM ${name} full log"
    cat "$log"
    echo "::endgroup::"
    python3 - "$name" "$rc" "$t0" "$t1" "$log" <<'PY'
import re, sys
name, rc, t0, t1, log = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
text = open(log, encoding="utf-8", errors="replace").read()
sums = re.findall(r"^=+ (.*(?:passed|failed|error).* in [0-9.]+s.*?) =+$", text, re.M)
print(f"XGY ARM {name} rc={rc} wall={t1 - t0:.1f}s :: " + " | ".join(sums))
for line in text.splitlines():
    if line.startswith("|") and ("ms" in line or "Step" in line):
        print(f"XGY PROF {name} {line}")
    if line.startswith("db_bench"):
        print(f"XGY DB {name} {line}")
slow = [l for l in text.splitlines() if re.match(r"^[0-9.]+s (call|setup|teardown) ", l)]
for l in slow[:12]:
    print(f"XGY SLOW {name} {l}")
PY
}

service_env() {
    export TEST_DATABASE_URL="postgresql://shekel_test:shekel_test@localhost:5432/shekel_test"
    export TEST_ADMIN_DATABASE_URL="postgresql://shekel_test:shekel_test@localhost:5432/postgres"
}

run_container_arm() {
    # $1 arm name, $2 extra docker flags ("" or a tmpfs mount)
    local name="$1" extra="$2"
    docker rm -fv xgy-pg >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    docker run -d --name xgy-pg -e POSTGRES_USER=shekel_test -e POSTGRES_PASSWORD=shekel_test \
        -e POSTGRES_DB=shekel_test -p 5433:5432 $extra "$PG_IMAGE" \
        -c fsync=off -c full_page_writes=off -c synchronous_commit=off >/dev/null
    until docker exec xgy-pg pg_isready -q -h 127.0.0.1 -U shekel_test; do sleep 0.2; done
    sleep 2
    until docker exec xgy-pg pg_isready -q -h 127.0.0.1 -U shekel_test; do sleep 0.2; done
    (
        export TEST_DATABASE_URL="postgresql://shekel_test:shekel_test@localhost:5433/shekel_test"
        export TEST_ADMIN_DATABASE_URL="postgresql://shekel_test:shekel_test@localhost:5433/postgres"
        arm "${name}-template" python scripts/build_test_template.py
        arm "${name}-dbbench" python tools/xgy_measure/db_bench.py
        arm "${name}" env SHEKEL_TEST_FIXTURE_PROFILE=1 pytest "${SLICE[@]}" -n 12 "${COMMON[@]}"
    )
    docker rm -fv xgy-pg >/dev/null 2>&1 || true
}

arm_A12() { (service_env && arm A12-service-n12 env SHEKEL_TEST_FIXTURE_PROFILE=1 pytest "${SLICE[@]}" -n 12 "${COMMON[@]}"); }
arm_A8() { (service_env && arm A8-service-n8 env SHEKEL_TEST_FIXTURE_PROFILE=1 pytest "${SLICE[@]}" -n 8 "${COMMON[@]}"); }
arm_A4() { (service_env && arm A4-service-n4 env SHEKEL_TEST_FIXTURE_PROFILE=1 pytest "${SLICE[@]}" -n 4 "${COMMON[@]}"); }
arm_E0() { run_container_arm E0-run-disk-n12 ""; }
arm_E() { run_container_arm E-run-tmpfs-n12 "--tmpfs /var/lib/postgresql:rw,size=6g"; }
arm_D() { arm D-testsh-n12 env SHEKEL_TEST_FIXTURE_PROFILE=1 ./scripts/test.sh "${SLICE[@]}" -n 12 "${COMMON[@]}"; }
arm_F() {
    local a b
    a=$(printf '%q ' "${HALF_A[@]}")
    b=$(printf '%q ' "${HALF_B[@]}")
    arm F-testsh-2clusters-x6 bash -c "
        ./scripts/test.sh $a -n 6 -m 'not docker' -p no:randomly -q --durations=0 &
        ./scripts/test.sh $b -n 6 -m 'not docker' -p no:randomly -q --durations=0 &
        wait"
}
arm_G() {
    arm G-testsh-4clusters-x3 bash -c "
        ./scripts/test.sh ${QUARTERS[0]} -n 3 -m 'not docker' -p no:randomly -q --durations=0 &
        ./scripts/test.sh ${QUARTERS[1]} -n 3 -m 'not docker' -p no:randomly -q --durations=0 &
        ./scripts/test.sh ${QUARTERS[2]} -n 3 -m 'not docker' -p no:randomly -q --durations=0 &
        ./scripts/test.sh ${QUARTERS[3]} -n 3 -m 'not docker' -p no:randomly -q --durations=0 &
        wait"
}

echo "XGY slice: ${#SLICE[@]} files; halves ${#HALF_A[@]}/${#HALF_B[@]}"

# Bake the test.sh image once, OUTSIDE any timed arm, and time the bake itself
# (what a CI job adopting scripts/test.sh would pay per job).
arm bake python scripts/build_test_db_image.py
arm D-dbbench ./scripts/test.sh tools/xgy_measure/db_bench.py -n 0 -p no:randomly -s -q

case "$REPLICATE" in
    1) ORDER=(A12 A8 A4 E0 E D F G) ;;
    2) ORDER=(G F D E E0 A4 A8 A12) ;;
    *) ORDER=(D A12 G A4 F E A8 E0) ;;
esac
for a in "${ORDER[@]}"; do
    echo "XGY running arm $a"
    "arm_$a"
done
