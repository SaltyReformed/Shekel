"""Shared helpers for audit scripts under ``scripts/audit/``.

Small utilities used by both ``seed_dast_users.py`` and
``idor_probe.py``. Kept narrow by design -- anything larger belongs in
its own module.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` atomically.

    Strategy: write to a sibling temp file, fsync it, then rename onto
    the destination. Same filesystem by construction so ``replace``
    is atomic. An aborted run does not leave a half-written JSON
    file at the destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        delete=False,
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        encoding="utf-8",
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_path = Path(handle.name)
    tmp_path.replace(path)
