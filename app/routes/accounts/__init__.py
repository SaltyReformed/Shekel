"""
Shekel Budget App -- Accounts Route Package

Split of the historical monolithic ``app/routes/accounts.py`` into a
package of per-sub-domain modules.  Commit 21 of the financial-
calculation audit follow-up remediation (F-1).

Direction: Option A (single blueprint, file-split by import).  One
``accounts_bp`` blueprint is shared across every sub-module; the
declaration lives in :mod:`app.routes.accounts._bp` (F-25 fix; see
that module's docstring for why the blueprint moved out of this init).
The per-sub-domain modules (``crud``, ``anchor``, ``types``,
``detail``) import the blueprint from ``_bp`` and register their
decorators against it.  Every URL is preserved verbatim from the
pre-split file; no ``url_for`` call site needed an edit and
``app/__init__.py`` continues to import ``accounts_bp`` from this
package by the same name (re-exported below).

Module map:

* :mod:`app.routes.accounts._bp` -- ``accounts_bp`` blueprint
  declaration (leaf module; F-25 cycle-break).
* :mod:`app.routes.accounts.crud` -- Account CRUD endpoints
  (``list_accounts``, ``new_account``, ``create_account``,
  ``edit_account``, ``update_account``, ``archive_account``,
  ``unarchive_account``, ``hard_delete_account``).
* :mod:`app.routes.accounts.anchor` -- Inline anchor-balance editors
  on the accounts list and the grid anchor true-up endpoints; both
  consumers of :func:`app.services.anchor_service.apply_anchor_true_up`.
* :mod:`app.routes.accounts.types` -- Account-type CRUD for the
  per-user custom catalogue (commit C-28 / F-044).
* :mod:`app.routes.accounts.detail` -- Per-account detail pages
  (``interest_detail``, ``update_interest_params``,
  ``checking_detail``); routed through ``balance_resolver`` per the
  E-25 / Commit-7 canonical-producer contract.

Shared validation helpers and Marshmallow schema singletons live in
:mod:`app.utils.account_validation` so every sub-module imports the
same instance, preserving the pre-split "one schema constructed at
module load" behaviour.
"""

# Re-export ``accounts_bp`` from the leaf declaration module so
# consumers that ``from app.routes.accounts import accounts_bp``
# (notably ``app/__init__.py`` at factory-time) continue to resolve
# without an edit.  Pre-F-25 the blueprint was declared inline here;
# moving it to ``_bp`` was the smallest cycle-break that preserved
# the public package surface.
from app.routes.accounts._bp import accounts_bp


# Import sub-modules for the side effect of registering their route
# decorators against ``accounts_bp``.  The ``noqa`` markers suppress
# the unused-import / out-of-order-import warnings that would
# otherwise fire on what is, by design, a deferred-import side-
# effect registration.
from app.routes.accounts import crud  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.accounts import anchor  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.accounts import types  # noqa: F401, E402  pylint: disable=wrong-import-position
from app.routes.accounts import detail  # noqa: F401, E402  pylint: disable=wrong-import-position


__all__ = ["accounts_bp"]
