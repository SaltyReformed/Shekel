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
* :mod:`app.routes.accounts.anchor` -- The grid and Net Worth Cockpit
  anchor true-up endpoints, consumers of
  :func:`app.services.anchor_service.apply_anchor_true_up`.
* :mod:`app.routes.accounts.reconcile` -- The outstanding-purchase panel
  ("which of these has your bank taken?") its context builder and both
  mounts share.  Split out of ``anchor`` when that module reached the
  1000-line ceiling; the boundary is WRITE DOOR against WHAT IS STILL
  OUTSTANDING, and it took two cross-module private names public with it
  (finding N-33's shape).
* :mod:`app.routes.accounts.difference` -- The true-up form's read-only
  DIFFERENCE preview (plan step X-f2-a, ruling R-EU): what the records produce
  for the day the form names, what was typed, and the gap.  Split out of
  ``anchor`` when plan step X-f2-b's acknowledgement re-key pushed that module
  past the 1000-line ceiling; the boundary is WRITE DOOR against a PREVIEW of
  a save that has not happened, and it took two names public with it.
* :mod:`app.routes.accounts.history` -- The Balance history card (plan step
  X-f2-b, ruling R-EV): every balance the user has told an account it held,
  beside what its records produced just before each one.  A third subject
  beside ``detail``'s page and ``anchor``'s write door, split for the reason
  ``reconcile`` was.
* :mod:`app.routes.accounts.statements` -- What the BANK said (plan step
  ``bank_import:X-f6a-1``, ruling R-FP): the statement import page and its
  write door.  A fourth subject beside ``anchor``'s assertion, ``reconcile``'s
  outstanding set and ``history``'s record of past assertions -- and the only
  one of them sourced from outside the app.  It RECORDS and does not
  reconcile.
* :mod:`app.routes.accounts.statement_matches` -- What the app DOES about it
  (plan step ``bank_import:X-f6a-2``, ruling R-FS): the review screen that
  proposes which of this account's rows each recorded line IS, and the two
  write doors that apply a reviewed pass and answer for a merchant nobody has
  answered for yet.  **The door that RELEASES one is no longer here**: it went
  with the accepted acts at ``bank_import:X-gf-2``, to
  ``_statement_release`` and the two surfaces that render it.  Its boundary against ``statements``
  is the one ``reconcile`` cuts against ``anchor``: a read of an outside record
  against the door that acts on it.  **It MOVES MONEY** -- an accepted match
  writes the bank's posted day onto every row it names.
* :mod:`app.routes.accounts.statement_register` -- What has already been
  DECIDED (plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**): the
  merchant answers already given and the matches already accepted, each with
  its undo.  Its boundary against ``statement_matches`` is the one that step
  exists for -- a QUEUE holds what is still being decided and a REGISTER holds
  what is not, and the two were one 578,523-byte page of which 76% was the
  register half.
* :mod:`app.routes.accounts.statement_workbench` -- The TOOL, not the queue
  (plan step ``bank_import:X-gf-3b``, ruling **bank_import:R-HC**): the
  hand-build match form, where the owner asserts a correspondence the matcher
  would not guess.  **It MOVES MONEY** -- recording a group writes the bank's
  posted day onto every row it names, which makes it the SECOND door here that
  does, beside ``statement_matches``.  Its boundary against that one is that a
  queue holds exceptions and this holds the tool three of them send the owner
  to; its two pick lists were 59% of the review page.
* :mod:`app.routes.accounts.bank_agreement` -- The two records SIDE BY SIDE
  (plan step ``bank_import:X-f6e-2``, ruling R-GF): a per-day comparison of
  what the app's own rows moved against what the bank's lines did, and of the
  two running balances.  It reports and never gates.  Its boundary against
  ``statements`` is ``difference``'s against ``anchor`` -- a read-only
  comparison of what a write door wrote is not that door's subject.
* :mod:`app.routes.accounts.types` -- Account-type CRUD for the
  per-user custom catalogue (commit C-28 / F-044).
* :mod:`app.routes.accounts.detail` -- Per-account detail pages.  The
  Fable 5 overhaul merged the former checking / interest pages into one
  ``cash_detail`` page serving every cash account kind (checking,
  ``has_interest`` types, and plain Savings / Credit Card / custom);
  ``checking_detail`` / ``interest_detail`` remain as redirect stubs.
  Also hosts ``update_interest_params`` and the ``property_detail`` /
  ``update_appreciation_params`` pair.  Balances route through the
  balance-at seam per the E-25 / Commit-8 canonical-producer contract.

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
from app.routes.accounts import crud  # noqa: F401, E402
from app.routes.accounts import reconcile  # noqa: F401, E402
from app.routes.accounts import history  # noqa: F401, E402
from app.routes.accounts import anchor  # noqa: F401, E402
from app.routes.accounts import difference  # noqa: F401, E402
from app.routes.accounts import types  # noqa: F401, E402
from app.routes.accounts import detail  # noqa: F401, E402
from app.routes.accounts import statements  # noqa: F401, E402
from app.routes.accounts import statement_matches  # noqa: F401, E402
from app.routes.accounts import statement_register  # noqa: F401, E402
from app.routes.accounts import statement_workbench  # noqa: F401, E402
from app.routes.accounts import bank_agreement  # noqa: F401, E402


__all__ = ["accounts_bp"]
