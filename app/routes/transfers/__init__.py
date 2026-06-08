"""
Shekel Budget App -- Transfer Route Package

CRUD for recurring transfer templates plus inline grid-cell endpoints for
individual transfers; returns HTMX fragments for the budget grid.  Split of the
historical monolithic ``app/routes/transfers.py`` into a package of per-concern
modules, following the ``app/routes/accounts/``, ``app/routes/salary/``, and
``app/routes/transactions/`` precedent.  One ``transfers_bp`` blueprint is
shared across every sub-module; the declaration lives in
:mod:`app.routes.transfers._bp` (cycle-break).  Each per-concern module imports
the blueprint from ``_bp`` and registers its route decorators against it.
Every URL and endpoint name is preserved verbatim from the pre-split file, so
no ``url_for`` call site, template, or ``app/__init__.py`` import needed an edit
(``app/__init__.py`` continues to import ``transfers_bp`` from this package by
the same name, re-exported below).

Module map:

* :mod:`app.routes.transfers._bp` -- ``transfers_bp`` declaration (leaf;
  cycle-break).
* :mod:`app.routes.transfers._helpers` -- shared Marshmallow schema singletons
  and the ownership / cell-render helpers.
* :mod:`app.routes.transfers.templates` -- recurring-template CRUD.
* :mod:`app.routes.transfers.forms` -- read-only grid-cell GET partials.
* :mod:`app.routes.transfers.mutations` -- single-instance mutations (edit /
  ad-hoc create / delete / mark-done / cancel), co-located so their shared
  service-update + cell-response parallel code stays intra-file.
"""

# Re-export ``transfers_bp`` from the leaf declaration module so consumers that
# ``from app.routes.transfers import transfers_bp`` (notably ``app/__init__.py``
# at factory time) resolve without an edit.
from app.routes.transfers._bp import transfers_bp

# Import sub-modules for the side effect of registering their route decorators
# against ``transfers_bp``.  The ``noqa`` markers suppress the unused-import /
# out-of-order-import warnings that would otherwise fire on what is, by design,
# a deferred-import side-effect registration.
from app.routes.transfers import templates  # noqa: F401, E402
from app.routes.transfers import forms  # noqa: F401, E402
from app.routes.transfers import mutations  # noqa: F401, E402


__all__ = ["transfers_bp"]
