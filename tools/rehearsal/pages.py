"""The pages (step X-cx's K1c-2): each deployed copy crawled by its own side's app as each of
its active users, and the rows after the crawl compared (``design.md`` s.3 item 1; ``k1c/plan.md``
s.3; ruling W2: the crawl runs inside the rows run, on its bench and its deploys).

THE CRAWL.  Each deployed copy (base, base again, cut) is cloned, and the clone is crawled by
the side probe ``side/crawl.py`` (:func:`.probes.crawl`), which says how a user is logged in,
what is followed and what stops it, under the run's pinned instant and secret key.  The users
are the clone's active ones (``auth.users.is_active``), in id order; each user's pay-period span
is the side's own to read.  The crawl's directory, its plan and every response it wrote, is in
the bench's scratch directory, gone at teardown.  Per user per copy the run prints the requests,
seconds and bytes, and the family that fetched the most.  A copy with no active user is
crawled by no one, which is printed, never refused: a dump of production always holds one.

THE ROWS AFTER THE CRAWL.  A page load can write (a GET that records a view, a stamp it
refreshes), so the crawled clones are compared as :mod:`.rowdiff` compares the deployed copies.
Base crawled against base again, crawled, must read identical: a difference there is the kit's
failure (exit 2).  Then base crawled against cut crawled, less every row the deploys' comparison
already reported as it was, so a row is not counted twice.

What the crawl cannot see is printed on every run (:data:`LIMITER`).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime

import psycopg

from tools.rehearsal import bench, probes
from tools.rehearsal.rowdiff import Moved, compare, summary

#: The most requests one user's crawl of one copy may fetch; a crawl still holding requests
#: there is refused (exit 2), naming the family that fetched the most.  PROVISIONAL (the
#: coordinator, 2026-10-20): 4 times the largest count the first nightly measurement reads.
CAP = 20_000
#: Printed on every run: what the crawl does not see (K1c's plan s.2, option A).
LIMITER = ("the rate limiter is off in the crawl; a cut that breaks its store or changes a "
           "limit is not seen")
#: The crawl's plan and summary, in its directory (``side/crawl.py``'s ``PLAN`` and
#: ``SUMMARY``).
_PLAN = "plan.json"
_SUMMARY = "summary.json"
_ACTIVE_USERS = "SELECT id FROM auth.users WHERE is_active ORDER BY id"


class Unmeasured(bench.BenchError):
    """The crawl could not measure a copy, or the crawled copies of base read differently:
    the kit's failure (exit 2), never the release's."""


def active_users(url: str) -> list[int]:
    """The ids of the copy's active users, in order."""
    with psycopg.connect(url) as connection:
        return [user for (user,) in connection.execute(_ACTIVE_USERS)]


def crawl(made: bench.Bench, label: str, state: bench.State, secret_key: str,
          moment: datetime) -> bench.State:
    """Clone ``state``'s deployed copy and crawl the clone as each of its active users,
    printing what each crawl read; the crawled clone.

    Raises:
        Unmeasured: when the crawl exits non-zero (its last words quoted).
        bench.BenchError: when the side probe cannot be run.
        psycopg.Error: when the copy cannot be cloned or read.
    """
    database = f"{state.database}_crawled"
    crawled = bench.State(state.name, state.commit, state.worktree, database,
                          made.clone(state.database, database), state.python)
    users = active_users(crawled.url)
    if not users:
        print(f"crawl {label}: no active user, so no page is crawled")
        return crawled
    directory = made.scratch / f"crawl-{database}"
    directory.mkdir()
    (directory / _PLAN).write_text(json.dumps({"cap": CAP, "users": users}), encoding="utf-8")
    began = time.monotonic()
    refused = probes.crawl(crawled, crawled.url, secret_key, moment, directory)
    if refused is not None:
        raise Unmeasured(f"the crawl of {label} refused: {refused}")
    _printed(label, json.loads((directory / _SUMMARY).read_text(encoding="utf-8")),
             time.monotonic() - began)
    return crawled


def _printed(label: str, read: dict, seconds: float) -> None:
    """Print what the crawl of ``label`` read: per user its requests, seconds, bytes and
    busiest family, then the whole crawl's time and the GET rules it reached."""
    for user, walked in read["users"].items():
        busiest = ""
        if walked["families"]:
            path, kind, count = walked["families"][0]
            busiest = f" busiest: {path} as a {kind} ({count})"
        print(f"crawl {label}: user {user}: {walked['requests']} requests, "
              f"{walked['seconds']:.2f} s, {walked['bytes']} bytes;{busiest}")
    rules = read["rules"]
    print(f"crawl {label}: {seconds:.2f} s; {rules['reached']} of {rules['get']} GET rules "
          "reached")


def _shown(row: Moved) -> tuple[str, str, str, str]:
    """``row`` as text, so rows read alike when they read alike (an image is unhashable)."""
    return row.table, row.key, repr(row.before), repr(row.after)


def after_the_crawl(crawled: dict[str, bench.State], reported: list[Moved],
                    line: datetime) -> list[Moved]:
    """The rows after the crawl: base crawled against base again, crawled, then base crawled
    against cut crawled, through ``line``; every row the second reads that ``reported`` does
    not already hold as it is.

    Raises:
        Unmeasured: when base crawled twice did not read the same, its rows on stderr.
        psycopg.Error: when a copy cannot be read.
    """
    base = crawled["base"]
    noise = compare(base.url, crawled["base, again"].url, line)
    print(f"noise gate after the crawl: {len(noise)} rows differ")
    if noise:
        print("\n".join(summary(noise, "base, crawled", "base again, crawled")),
              file=sys.stderr)
        raise Unmeasured("base crawled twice did not read the same, so the rows after the "
                         "crawl measure nothing")
    known = {_shown(row) for row in reported}
    return [row for row in compare(base.url, crawled["cut"].url, line)
            if _shown(row) not in known]
