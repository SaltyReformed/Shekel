"""
Diagnostic script: monitor PostgreSQL connections during a pytest run.

Polls pg_stat_activity every 2 seconds and logs connection count,
states, and any queries that have been running longer than 5 seconds.
Run this in a separate terminal WHILE pytest is running.
"""
import json
import time
import subprocess
import sys
from datetime import datetime


def poll_connections():
    """Poll the test database for active connections and long queries."""
    query = (
        "SELECT json_agg(json_build_object("
        "'pid', pid, "
        "'state', state, "
        "'wait_event_type', wait_event_type, "
        "'wait_event', wait_event, "
        "'duration_sec', EXTRACT(EPOCH FROM (now() - query_start)), "
        "'query', LEFT(query, 100)"
        ")) "
        "FROM pg_stat_activity "
        "WHERE datname = 'shekel_test' AND pid <> pg_backend_pid();"
    )
    cmd = [
        "docker", "exec", "shekel-test-db",
        "psql", "-U", "shekel_user", "-d", "shekel_test",
        "-t", "-A", "-c", query,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return result.stdout.strip()


def main():
    """Main loop: poll connections and print diagnostics."""
    print(f"[{datetime.now().isoformat()}] Starting connection monitor...")
    # Track the high-water mark for connection count.
    max_connections = 0

    while True:
        try:
            raw = poll_connections()
            if raw and raw != "":
                connections = json.loads(raw)
                count = len(connections) if connections else 0
                max_connections = max(max_connections, count)

                # Flag any connections idle in transaction for over 5 seconds.
                stuck = [
                    c for c in (connections or [])
                    if c.get("state") == "idle in transaction"
                    and (c.get("duration_sec") or 0) > 5
                ]

                # Flag any connections waiting on locks.
                waiting = [
                    c for c in (connections or [])
                    if c.get("wait_event_type") == "Lock"
                ]

                timestamp = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{timestamp}] Connections: {count} "
                    f"(max: {max_connections})"
                )

                if stuck:
                    print(f"  WARNING: {len(stuck)} idle-in-transaction:")
                    for s in stuck:
                        print(
                            f"    PID {s['pid']}: "
                            f"{s['duration_sec']:.1f}s - {s['query']}"
                        )

                if waiting:
                    print(f"  WARNING: {len(waiting)} waiting on locks:")
                    for w in waiting:
                        print(
                            f"    PID {w['pid']}: "
                            f"{w['wait_event']} - {w['query']}"
                        )
            else:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"Connections: 0"
                )

        except subprocess.TimeoutExpired:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"WARNING: psql query timed out (test-db may be overloaded)"
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"  Error polling: {exc}")

        time.sleep(2)


if __name__ == "__main__":
    main()
