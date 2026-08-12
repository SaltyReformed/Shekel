"""
Shekel Budget App -- Recurring Route Package

The unified Recurring surface and the recurring-transaction (template) CRUD
behind it.  Split of the historical single module ``app/routes/templates.py``
when it reached the 1,000-line module cap, following the
``app/routes/accounts/``, ``app/routes/salary/``, ``app/routes/transactions/``
and ``app/routes/transfers/`` precedent.  One ``templates_bp`` blueprint is
shared across every sub-module; the declaration lives in
:mod:`app.routes.templates._bp` (cycle-break).  Every URL and endpoint name is
preserved verbatim from the pre-split file, so no ``url_for`` call site,
template, or ``app/__init__.py`` import needed an edit (the factory continues to
``importlib.import_module("app.routes.templates")`` and read ``templates_bp``
off it, re-exported below).

**The seam is not arbitrary.**  The Recurring SURFACE lists every recurring
definition a user has -- income, expense AND transfer templates -- so it reads
both template kinds and is not transaction-template CRUD at all; it was the one
part of the pre-split module its own docstring ("CRUD pages for transaction
templates") did not describe.

Module map:

* :mod:`app.routes.templates._bp` -- ``templates_bp`` declaration (leaf;
  cycle-break).
* :mod:`app.routes.templates.surface` -- the unified ``/templates`` Recurring
  page and its Monthly / Per-paycheck unit toggle, over both template kinds.
* :mod:`app.routes.templates.crud` -- recurring-TRANSACTION template CRUD:
  create, edit, update, archive, unarchive, hard-delete, and the kind-agnostic
  recurrence-preview fragment endpoint.
"""

# Re-export ``templates_bp`` from the leaf declaration module so consumers that
# read it off this package (notably ``app/__init__.py`` at factory time) resolve
# without an edit.
from app.routes.templates._bp import templates_bp

# Import sub-modules for the side effect of registering their route decorators
# against ``templates_bp``.  The ``noqa`` markers suppress the unused-import /
# out-of-order-import warnings that would otherwise fire on what is, by design,
# a deferred-import side-effect registration.
from app.routes.templates import surface  # noqa: F401, E402
from app.routes.templates import crud  # noqa: F401, E402


__all__ = ["templates_bp"]
