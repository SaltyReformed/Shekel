"""Split the collected suite into disjoint shards, one per CI runner.

CI runs the suite as several parallel jobs (plan step ``bank_import:X-gy``,
ledger BI-496).  Each job exports ``SHEKEL_TEST_SHARD=<index>/<total>`` and
``tests/conftest.py``'s collection hook hands its items to :func:`apply_shard`,
which keeps only this shard's share and reports the rest as deselected.  Unset,
nothing is deselected: a local run is the whole suite, as it always was.

**The shards partition the suite BY CONSTRUCTION.**  An item's shard is a
function of that item alone -- a stable hash of its key -- never its position
in a list.  A position-based split (round-robin over sorted ids) is only a
partition while every job collects the identical list; one extra item in one
job shifts every index after it, and the shift both runs some tests twice and
runs others nowhere, with every job green.  Here an item the other jobs do not
collect lands in exactly one shard anyway, and no other item moves.

**An ``xdist_group`` is kept whole.**  The marker is a CO-LOCATION contract, not
only a lock on cluster state: ``test_a_seeded_start_state_is_built_once.py``
asserts facts about its group's whole session, and the first measured split
(one hash per node id, round 2 of X-gy's runner A/B on 2026-09-22) scattered
that group across shards and failed it closed.  So a grouped item is keyed by
its group, every member shares one key, and the whole group lands on one shard
-- where ``--dist=loadgroup`` puts it on one worker exactly as an unsharded
run does.

**The hash is SHA-256, not ``hash()``.**  Python salts ``str`` hashing per
process (``PYTHONHASHSEED``), so ``hash()`` would give every CI job a different
split of the same suite.  Every job must compute the same assignment.

Measured 2026-09-22 on the hosted runner: a prototype of this module split the
15,166 items its tree collected into six shards of 2,429-2,602, whose union was
the collection exactly with no item in two (run 35808935180); this module's own
six shards then ran 2,443-2,612 each, 15,236 in all, exactly the local
collection of its tree (run 35815066395).
"""
import hashlib

import pytest

#: The environment variable a CI job sets to ``<index>/<total>``.
SHARD_ENV = "SHEKEL_TEST_SHARD"


def parse_shard(spec):
    """Read ``"<index>/<total>"`` as ``(index, total)``, or refuse it.

    A malformed value is a usage error rather than "no sharding": silently
    running the whole suite, or nothing, in a job that asked for one share
    would be the failure this module exists to make impossible.

    Args:
        spec: The environment variable's value, e.g. ``"0/6"``.

    Returns:
        ``(index, total)`` with ``0 <= index < total``.

    Raises:
        pytest.UsageError: The value is not two decimal integers separated by
            ``/``, or ``total`` is not positive, or ``index`` is out of range.
    """
    parts = spec.split("/")
    if len(parts) != 2 or not all(part.isdecimal() for part in parts):
        raise pytest.UsageError(
            f"{SHARD_ENV}={spec!r} is not '<index>/<total>' (for example '0/6')"
        )
    index, total = int(parts[0]), int(parts[1])
    if total < 1 or index >= total:
        raise pytest.UsageError(
            f"{SHARD_ENV}={spec!r} names shard {index} of {total}; the index "
            f"must be 0 to {total - 1}"
        )
    return index, total


def shard_key(item):
    """Return what ``item`` is sharded by: its ``xdist_group``, else its node id.

    The group name is read exactly as pytest-xdist reads it for
    ``--dist=loadgroup`` -- the marker's first argument, else its ``name``
    keyword, else ``"default"`` -- and an item carrying several groups is
    keyed by all of them, as xdist names it.

    Args:
        item: A collected pytest item.

    Returns:
        The string the item's shard is derived from.
    """
    groups = sorted({
        mark.args[0] if mark.args else mark.kwargs.get("name", "default")
        for mark in item.iter_markers("xdist_group")
    })
    if groups:
        return "xdist_group:" + "_".join(groups)
    return item.nodeid


def shard_of(key, total):
    """Return the shard, in ``range(total)``, that ``key`` belongs to.

    Args:
        key: A :func:`shard_key` value.
        total: The number of shards.

    Returns:
        The shard index.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total


def apply_shard(config, items, spec):
    """Keep this shard's items in ``items`` and report the rest as deselected.

    Called from ``tests/conftest.py``'s ``pytest_collection_modifyitems``.

    Args:
        config: The pytest config, whose hook reports the deselection.
        items: The collected items; narrowed IN PLACE, as pytest requires.
        spec: The ``SHEKEL_TEST_SHARD`` value, or ``None`` when unset, in
            which case nothing is deselected.
    """
    if spec is None:
        return
    index, total = parse_shard(spec)
    keep, drop = [], []
    for item in items:
        (keep if shard_of(shard_key(item), total) == index else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
