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


def pytest_collection_modifyitems(config, items):
    """Keep only this shard's items; report the rest as deselected."""
    spec = os.environ.get("XGY_SHARD")
    if not spec:
        return
    index, total = (int(x) for x in spec.split("/"))
    keep, drop = [], []
    for item in items:
        (keep if shard_of(item.nodeid, total) == index else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
