"""
Shekel Budget App -- Loan Route Package

Unified dashboard, parameter updates, escrow / rate-history management, payoff
and refinance calculators, and recurring-payment-transfer creation for all
installment loan account types.  Split of the historical monolithic
``app/routes/loan.py`` into a package of per-concern modules, following the
``app/routes/accounts/``, ``app/routes/salary/``, ``app/routes/transactions/``,
and ``app/routes/transfers/`` precedent.  One ``loan_bp`` blueprint is shared
across every sub-module; the declaration lives in :mod:`app.routes.loan._bp`
(cycle-break).  Each per-concern module imports the blueprint from ``_bp`` and
registers its route decorators against it.  Every URL and endpoint name is
preserved verbatim from the pre-split file, so no ``url_for`` call site,
template, or ``app/__init__.py`` import needed an edit (``app/__init__.py``
continues to import ``loan_bp`` from this package by the same name, re-exported
below).

Module map:

* :mod:`app.routes.loan._bp` -- ``loan_bp`` declaration (leaf; cycle-break).
* :mod:`app.routes.loan._helpers` -- shared Marshmallow schema singletons, the
  loan-account / resolver-state / full-context loaders, and the chart-balance
  utilities.
* :mod:`app.routes.loan.dashboard` -- the loan detail page (GET) and its
  context-building helpers.
* :mod:`app.routes.loan.params` -- loan-parameter CRUD and the dated
  balance true-up.
* :mod:`app.routes.loan.escrow_rates` -- escrow-component and rate-history
  management (HTMX partials sharing the OOB payment-summary tail).
* :mod:`app.routes.loan.calculators` -- the payoff and refinance what-if
  calculators (HTMX partials).
* :mod:`app.routes.loan.payment_transfer` -- recurring monthly payment-transfer
  creation.
"""

# Re-export ``loan_bp`` from the leaf declaration module so consumers that
# ``from app.routes.loan import loan_bp`` (notably ``app/__init__.py`` at
# factory time) resolve without an edit.
from app.routes.loan._bp import loan_bp

# Import sub-modules for the side effect of registering their route decorators
# against ``loan_bp``.  The ``noqa`` markers suppress the unused-import /
# out-of-order-import warnings that would otherwise fire on what is, by design,
# a deferred-import side-effect registration.
from app.routes.loan import dashboard  # noqa: F401, E402
from app.routes.loan import params  # noqa: F401, E402
from app.routes.loan import escrow_rates  # noqa: F401, E402
from app.routes.loan import calculators  # noqa: F401, E402
from app.routes.loan import payment_transfer  # noqa: F401, E402


__all__ = ["loan_bp"]
