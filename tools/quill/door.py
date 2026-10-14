"""Quill's one public door: each name another package reads from quill's private modules.

``shekel-private-module-import`` refuses another package's import of a private module,
so a name another package must read is re-exported here, and this module holds no logic
and no name of its own (the shape of ``tools/plan_gate/registries.py``, ruling
``balance:R-BAL244``; this door decided by the coordinator under ruling
``balance:R-BAL207``, 2026-10-13, as ``plan_labels``, and renamed when the release
tool's card reader joined it, 2026-10-14).  ``quill.py`` is a public module and is
imported directly, never through here.

Each name, and who reads it:

- ``step_labels``: ``tools/release``'s pre-cutover arm (``_carried``), for each step row
  it reads: its money flag and whether it is a deploy-together container, by the ONE
  producer the migration writes a step card's labels with (CLAUDE.md rule 14).  The
  cutover (L8) deletes this name with ``_migrate_*.py`` and that arm.
- ``Tracker``: the type of the tracker ``tools/release`` reads for a cut after the
  cutover (``_plan``'s and ``release``'s connection to it, ``_cards``' reads of it); the
  connection itself is ``quill.CONNECT``.
- ``with_closure``: ``tools/release._cards``, for the cards a release's trailers name and
  every card a decision over them reads, by quill's one closure read.
- ``leaves_below`` and ``dropped``: ``tools/release._cards``, for a deploy-together
  group's leaves by quill's own leaf rule, less each one dropped.
- ``is_work``: ``tools/release._cards``, which refuses a trailer naming a card no commit
  ships (a container, a ruling, a question), by quill's one predicate.
- ``GitHubError`` and ``TrackerError``: ``tools/release.release``, which reports a failed
  read of the tracker as a release judged on nothing.
- ``FakeTracker``: ``tools/release``'s tests, which drive the card reader against quill's
  own in-memory tracker.  Its cost, accepted so quill keeps ONE door: every importer of
  this module also loads quill's fake (``_fake``, which imports ``quill`` and
  ``tools.ci.scratch``); nothing calls it outside the tests.
"""
from __future__ import annotations

from tools.quill._command import with_closure
from tools.quill._fake import FakeTracker
from tools.quill._github import GitHubError
from tools.quill._migrate_source import step_labels
from tools.quill._state import dropped, is_work, leaves_below
from tools.quill._tracker import Tracker, TrackerError

__all__ = [
    "FakeTracker",
    "GitHubError",
    "Tracker",
    "TrackerError",
    "dropped",
    "is_work",
    "leaves_below",
    "step_labels",
    "with_closure",
]
