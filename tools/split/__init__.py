"""Split a test module that has outgrown the line budget into a package, as a PROVED pure move
(step ``balance:X-er``, ruling ``R-BAL301``: every Python file under 1,000 lines).

The model never carries the code: a plan names which top-level nodes go to which part, and
the tool moves source lines by their AST line ranges.  ``tests/<dir>/<module>.py`` becomes
``tests/<dir>/<module>/``: ``__init__.py`` (the module's docstring, verbatim),
``conftest.py`` (its module-level fixtures), ``_helpers.py`` (its helpers and constants, and
the mixin of any class split across parts) and ``test_<subject>.py`` parts, each docstring
closed by the tool's own provenance paragraph.  Each part imports only what it reads.  A plan
the tool cannot prove is REFUSED; it never guesses.

**The proof a split commit quotes** (``split.py prove``, printed only when every arm passed):
every moved node verbatim and ``ast.dump``-identical and held exactly once, in source order
(:mod:`._prove`); no fixture the source's tests set up whose reach the split would change, a
parent conftest's or plugin's scoped beyond one test or one imported into the source (read
at ``record`` through pytest's own hook, :mod:`._observe`); the same tests collected under a
total, one-to-one old -> new node-id map; every test ending the same way, skip / xfail /
xpass included, with all parts run together AND each part run alone; every pylint message in
the parts answered by the source line it came from (an import-sequence message by the
source's import block); every re-pointed importer changed on its re-pointed imports alone,
with no new message on any other line and each new one on those imports named; every
duplicate-code pair between parts one the split revealed, never one it could have created;
the plan gate green; and every line of the working tree, tracked or not, still naming the old
module, listed (:mod:`._dynamic`).

**Which pointers to the old path a split commit edits** (the coordinator's ruling on the
split session's forks, 2026-10-15).  The same commit that moves a file edits every LIVE
pointer to it.  A pointer is live when its document is live AND the line says where
something IS: a class or test to go and read.  Code comments, test docstrings, a live
registry row's path text and a live design audit's class pointer are edited.  A dated
record, a log and a stale plan keep the path they held on their date: rewritten, they
would name a path that did not exist then.  Nothing under ``archive/`` or ``historical/``,
and nothing that opens with the archive banner (:mod:`tools.ci.archive`, the plan gate's
rule), is ever edited.  The census (:func:`._run.census`) lists every remaining line but the
package's own provenance; the commit body names each one not edited, with its reason (dated
record, log, or stale plan), so a reader finds it in git.

**When a split class's slices OPEN alike, move the slice boundary.**  Every slice of a split
class begins with the same generated line, ``class <Name>(_<Name>Members):``.  When the
tests that open two slices begin with the same code, pylint's duplicate-code run extends up
onto that line in both parts, and the duplicate-code arm (:mod:`._similar`) refuses the pair as
CREATED, because it touches a generated line.  That refusal is correct and fails closed; the
remedy is a plan whose slices start at tests that differ, never a loosened arm.
"""
