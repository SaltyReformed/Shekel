"""Database micro-benchmark for X-gy host-vs-runner comparison.

Reads TEST_ADMIN_DATABASE_URL (the admin DSN of whatever cluster the caller
set up) and measures, on that one path:

  connect    N fresh connections, each closed at once: fork + auth cost
  roundtrip  N ``SELECT 1`` on one connection: per-statement latency
  clone      N ``DROP DATABASE ... WITH (FORCE)`` + ``CREATE DATABASE ...
             TEMPLATE shekel_test_template STRATEGY WAL_LOG`` cycles, each on
             its own admin connection exactly as tests/conftest.py's ``db``
             fixture issues them

Prints the median and p90 of each, in milliseconds.  Run as a script
(``python db_bench.py``) or collected by pytest (``test_db_bench``).
"""
import os
import statistics
import time

import psycopg2
from psycopg2 import sql

TEMPLATE = "shekel_test_template"
TARGET = "xgy_bench_clone"


def _stats(label, samples_ms):
    samples_ms = sorted(samples_ms)
    p90 = samples_ms[int(len(samples_ms) * 0.9) - 1]
    print(
        f"db_bench {label}: n={len(samples_ms)} "
        f"median={statistics.median(samples_ms):.3f}ms p90={p90:.3f}ms "
        f"total={sum(samples_ms) / 1000:.2f}s"
    )


def bench(dsn, n_connect=200, n_roundtrip=5000, n_clone=60):
    samples = []
    for _ in range(n_connect):
        t0 = time.perf_counter()
        conn = psycopg2.connect(dsn)
        conn.close()
        samples.append((time.perf_counter() - t0) * 1000)
    _stats("connect", samples)

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    samples = []
    with conn.cursor() as cur:
        for _ in range(n_roundtrip):
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            samples.append((time.perf_counter() - t0) * 1000)
    conn.close()
    _stats("roundtrip", samples)

    drops, creates = [], []
    for _ in range(n_clone):
        for bucket, stmt in (
            (drops, sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(TARGET))),
            (creates, sql.SQL("CREATE DATABASE {} TEMPLATE {} STRATEGY WAL_LOG").format(
                sql.Identifier(TARGET), sql.Identifier(TEMPLATE))),
        ):
            t0 = time.perf_counter()
            admin = psycopg2.connect(dsn)
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(stmt)
            admin.close()
            bucket.append((time.perf_counter() - t0) * 1000)
    _stats("drop(incl connect)", drops)
    _stats("clone(incl connect)", creates)
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
            sql.Identifier(TARGET)))
        cur.execute("SELECT name, setting FROM pg_settings WHERE name = ANY(%s) ORDER BY name",
                    (["fsync", "full_page_writes", "synchronous_commit", "password_encryption",
                      "lock_timeout", "statement_timeout", "shared_buffers"],))
        print("db_bench settings:", dict(cur.fetchall()))
        cur.execute("SELECT pg_size_pretty(pg_database_size(%s))", (TEMPLATE,))
        print("db_bench template size:", cur.fetchone()[0])
    admin.close()


def test_db_bench():
    """Collected by pytest so the bench can run under scripts/test.sh."""
    bench(os.environ["TEST_ADMIN_DATABASE_URL"])


if __name__ == "__main__":
    bench(os.environ["TEST_ADMIN_DATABASE_URL"])
