"""The migration's step-label producer, as one public read-only door for the release tool.

The release tool (``tools/release``, step X-cx's L9a) reads, before the cutover, whether
a step a release carries moves money and which deploy-together group it is a leaf of.
It must read them through the ONE producer the migration writes a step card's labels
with (CLAUDE.md rule 14), so a row read before the cutover gets the label the migration
would give it, by one rule and not two.  That producer lives in this
package's private ``_migrate_source``, which ``shekel-private-module-import`` refuses
another package's import of, so this module re-exports it, and holds no logic and no
name of its own (the shape of ``tools/plan_gate/registries.py``, ruling
``balance:R-BAL244``; this door decided by the coordinator under ruling
``balance:R-BAL207``, 2026-10-13).

Each name, and who reads it:

- ``step_labels``: ``tools/release``'s pre-cutover arm, for each step row it reads: its
  money flag and whether it is a deploy-together container.

The cutover (L8) deletes this module with ``_migrate_*.py`` and the release tool's
pre-cutover arm.
"""
from __future__ import annotations

from tools.quill._migrate_source import step_labels

__all__ = ["step_labels"]
