"""The release rehearsal kit (step X-cx's K1, rulings balance:R-BAL314 and R-BAL315): what a
release changes on production's own data, measured on that morning's copy, base against cut.

It has two halves, each with its own import rule, both held by :mod:`.test_imports`:

* **The orchestrator** is every module here but ``side/``.  It runs in the kit's own tree's
  venv and imports the standard library, ``psycopg`` (the one driver, which it reads rows
  with by SQL) and ``tools.ci``'s public modules.  It never imports ``app``, ``scripts`` or
  another tools package: the kit judges two versions of ``app``, and importing one into the
  judge would make the judge run a third.  What it needs from ``scripts`` (the private
  cluster, the test-db image) it runs as a subprocess.
* **The side probe** is ``side/``, a script run BY PATH under each side's own venv, with
  that side's worktree as its directory.  It imports the standard library, ``flask``,
  ``time_machine`` and the few names of ``app`` that :data:`.test_imports.SIDE_APP_NAMES`
  lists, and never ``tools``: the side's tree holds a ``tools/`` of its own, which would
  shadow the kit's.  It runs on both base and cut, so it reads only that small surface: the
  factory and the ``db`` extension, and for the crawl the limiter, the login's own two calls
  and the pay calendar.  A cut that renames one of them fails its own side's probe, by name.
"""
