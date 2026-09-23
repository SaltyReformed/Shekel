"""X-gy MEASUREMENT ONLY: deselect every item not in shard ``XGY_SHARD=i/n``.

An item's shard is a function of its OWN node id alone (a stable hash, never
Python's salted ``hash()``), so the shards partition whatever is collected by
construction: no index into a sorted list that one extra item could shift.
Loaded with ``-p xgy_shard`` and ``PYTHONPATH=tools/xgy_measure``.
"""
import hashlib
import os


def shard_of(nodeid, total):
    """Return the shard ``nodeid`` belongs to, in ``range(total)``."""
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total


def shard_key(item):
    """What an item is sharded BY: its xdist_group, else its own node id.

    An ``xdist_group`` is a co-location contract, not only a cluster-state
    lock: ``test_a_seeded_start_state_is_built_once.py`` asserts facts about
    its group's whole session, and round 2 split that group across shards and
    failed it.  Every member of a group therefore shares one key, so the whole
    group lands on one shard -- where ``--dist=loadgroup`` puts it on one
    worker exactly as an unsharded run does.
    """
    names = sorted({
        mark.args[0] if mark.args else mark.kwargs.get("name", "default")
        for mark in item.iter_markers("xdist_group")
    })
    if names:
        return "xdist_group:" + "_".join(names)
    return item.nodeid


def pytest_collection_modifyitems(config, items):
    """Keep only this shard's items; report the rest as deselected."""
    spec = os.environ.get("XGY_SHARD")
    if not spec:
        return
    index, total = (int(x) for x in spec.split("/"))
    keep, drop = [], []
    for item in items:
        (keep if shard_of(shard_key(item), total) == index else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
