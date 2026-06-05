"""
Shekel Budget App -- Salary Route Package

Split of the historical monolithic ``app/routes/salary.py`` (1600+ lines)
into a package of per-sub-domain modules, following the
``app/routes/accounts/`` precedent.  One ``salary_bp`` blueprint is shared
across every sub-module; the declaration lives in
:mod:`app.routes.salary._bp` (cycle-break, mirrors ``accounts/_bp.py``).
Each per-domain module imports the blueprint from ``_bp`` and registers
its route decorators against it.  Every URL and endpoint name is preserved
verbatim from the pre-split file, so no ``url_for`` call site, template,
or ``app/__init__.py`` import needed an edit (``app/__init__.py`` continues
to import ``salary_bp`` from this package by the same name, re-exported
below).

Module map:

* :mod:`app.routes.salary._bp` -- ``salary_bp`` blueprint declaration
  (leaf module; cycle-break).
* :mod:`app.routes.salary._helpers` -- shared Marshmallow schema
  singletons, form-field allowlists, unique-constraint name constants,
  and the private helpers used across handlers
  (``_regenerate_salary_transactions``, ``_regenerate_all_salary_transactions``,
  ``_compute_total_pre_tax``, ``_reject_if_rates_inconsistent``, the
  ``_render_*_partial`` / ``_respond_after_*_change`` HTMX responders,
  ``_get_investment_accounts``).
* :mod:`app.routes.salary.profiles` -- salary-profile CRUD
  (``list_profiles``, ``new_profile``, ``create_profile``, ``edit_profile``,
  ``update_profile``, ``delete_profile``).
* :mod:`app.routes.salary.items` -- raise and deduction add/edit/delete
  (the two parallel salary line-item families, co-located).
* :mod:`app.routes.salary.views` -- paycheck breakdown + projection views.
* :mod:`app.routes.salary.calibration` -- the pay-stub calibration flow
  (form/preview/confirm/delete).
* :mod:`app.routes.salary.tax_config` -- state-tax and FICA config updates.
"""

# Re-export ``salary_bp`` from the leaf declaration module so consumers
# that ``from app.routes.salary import salary_bp`` (notably
# ``app/__init__.py`` at factory time) resolve without an edit.
from app.routes.salary._bp import salary_bp

# Import sub-modules for the side effect of registering their route
# decorators against ``salary_bp``.  The ``noqa`` markers suppress the
# unused-import / out-of-order-import warnings that would otherwise fire
# on what is, by design, a deferred-import side-effect registration.
from app.routes.salary import profiles  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.salary import items  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.salary import views  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.salary import calibration  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.salary import tax_config  # noqa: F401, E402  pylint: disable=wrong-import-position


__all__ = ["salary_bp"]
