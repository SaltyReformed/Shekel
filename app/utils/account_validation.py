"""
Shekel Budget App -- Account Validation Helpers

Shared validation helpers and Marshmallow schema singletons for the
``app.routes.accounts`` package.  Extracted in Commit 21 of the
financial-calculation follow-up remediation (F-1) when the monolithic
``app/routes/accounts.py`` was split into per-sub-domain modules.

The helpers fall into two groups:

* **Multi-tenant ``ref.account_types`` ownership** (commit C-28 /
  F-044).  ``_visible_account_types`` enumerates the seeded built-ins
  plus the caller's own custom types; ``_owned_account_type`` looks
  up a single owned row for per-type mutation routes;
  ``_account_type_is_visible`` is the route-layer guard that prevents
  a forged form from re-parenting an account onto another owner's
  custom type.

* **``Account`` update validation** (consolidates the four early-
  return gates of the ``update_account`` route into a single helper).
  ``_validate_update_account`` returns a ``(data, failure)`` tuple
  so the route owns the flash + redirect composition while this
  module owns the business validation.

The six Marshmallow schema singletons (``_anchor_schema``,
``_create_schema``, ``_update_schema``, ``_type_create_schema``,
``_type_update_schema``, ``_interest_params_schema``) are also kept
here so every sub-domain module in the accounts package imports the
same instance -- preserving the pre-split behaviour where each
schema was constructed exactly once at module load.

Services boundary: this module is a route-layer helper, not a
service.  It imports the SQLAlchemy ``db`` proxy and reads the
SQLAlchemy ``Account`` / ``AccountType`` rows directly because it
exists to keep route bodies thin.  No Flask request globals are
touched -- callers pass the current user id in explicitly.
"""

from app.extensions import db
from app.models.account import Account
from app.models.ref import AccountType
from app.schemas.validation import (
    AccountCreateSchema,
    AccountTypeCreateSchema,
    AccountTypeUpdateSchema,
    AccountUpdateSchema,
    AnchorUpdateSchema,
    InterestParamsUpdateSchema,
)


# Marshmallow schema singletons.  Constructed once per process and
# imported by every sub-module of ``app.routes.accounts`` so the pre-
# split behaviour ("one schema instance shared across every endpoint
# that consumes it") is preserved.
_anchor_schema = AnchorUpdateSchema()
_create_schema = AccountCreateSchema()
_update_schema = AccountUpdateSchema()
_type_create_schema = AccountTypeCreateSchema()
_type_update_schema = AccountTypeUpdateSchema()
_interest_params_schema = InterestParamsUpdateSchema()


def _visible_account_types(user_id):
    """Return the account types this user is allowed to see.

    Built-in types (``user_id IS NULL``) are visible to every owner;
    a user's own custom types are visible only to them.  Other
    owners' custom types are excluded so the settings page and the
    account-form dropdown cannot leak the existence of one user's
    custom catalogue to another user (commit C-28 / F-044).

    Args:
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        list[AccountType] -- ordered by ``name`` for stable rendering.
        Includes the seeded built-ins (each ``AcctTypeEnum`` member)
        plus every row whose ``user_id`` matches the caller.
    """
    return (
        db.session.query(AccountType)
        .filter(db.or_(
            AccountType.user_id.is_(None),
            AccountType.user_id == user_id,
        ))
        .order_by(AccountType.name)
        .all()
    )


def _owned_account_type(type_id, user_id):
    """Return the account type if owned by this user, else ``None``.

    Used by the per-type mutation routes (``update``, ``delete``) to
    enforce the C-28 ownership guard.  A ``None`` return collapses
    the three "type does not exist", "type belongs to another owner",
    and "type is a seeded built-in" cases into a single
    indistinguishable response, matching the project's
    "404 for both 'not found' and 'not yours'" security rule.

    Args:
        type_id: Primary key of the candidate ``ref.account_types`` row.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        AccountType when the row exists and ``user_id`` matches;
        ``None`` otherwise.
    """
    account_type = db.session.get(AccountType, type_id)
    if account_type is None or account_type.user_id != user_id:
        return None
    return account_type


def _account_type_is_visible(type_id, user_id):
    """Return True iff ``type_id`` references a seeded or owned type.

    Account create/update accept ``account_type_id`` from the form.
    Before C-28 every type was global, so the FK constraint alone
    sufficed; afterwards an owner forging a POST could attach their
    new account to another owner's custom type, leaking that type's
    existence and producing a cross-user FK reference that C-29's
    re-parenting guard does not cover for the account row itself.
    This helper is the route-layer guard that pairs with the new
    multi-tenant ownership rule on ``ref.account_types``: the
    ``account_type_id`` must point at a seeded built-in
    (``user_id IS NULL``) or at one of the caller's own types.

    Args:
        type_id: Submitted ``ref.account_types.id`` value.
        user_id: ``auth.users.id`` of the current owner.

    Returns:
        bool -- True when the type exists and is either seeded or
        owned by *user_id*; False otherwise.  Identical False for
        "does not exist" and "owned by another user" so the
        response cannot be used to enumerate other owners' types.
    """
    account_type = db.session.get(AccountType, type_id)
    if account_type is None:
        return False
    return account_type.user_id is None or account_type.user_id == user_id


def _validate_update_account(account, form, user_id):
    """Run every non-mutating gate for ``update_account`` in one place.

    The route grew enough early-return guards (schema validation,
    C-28 multi-tenant ``account_type_id`` check, stale-form
    ``version_id`` check, duplicate-name check) to trip Pylint's
    ``too-many-return-statements`` after C-28 added one more.
    Consolidating the gates into a single helper that returns a
    ``(data, failure)`` tuple lets the route have one validation
    early return instead of four, without losing the per-condition
    flash distinctions.

    Args:
        account: The ``Account`` row about to be mutated.
        form: The submitted ``request.form`` mapping.
        user_id: ``auth.users.id`` of the current owner (passed
            explicitly so this helper does not depend on Flask
            request globals -- matches the project's Routes-pass-
            primitives-into-services style).

    Returns:
        A two-tuple ``(data, failure)``.  When validation passes,
        ``data`` is the schema-loaded payload (with ``version_id``
        already popped) and ``failure`` is ``None``.  When any gate
        rejects, ``data`` is an empty dict and ``failure`` is a
        ``(message, category)`` tuple ready to feed to
        :func:`flask.flash`.  The two-tuple form keeps the helper
        a pure function -- it never touches the response layer.
    """
    if _update_schema.validate(form):
        return {}, (
            "Please correct the highlighted errors and try again.",
            "danger",
        )

    data = _update_schema.load(form)

    # Multi-tenant guard (commit C-28 / F-044): when the form
    # re-parents the account to a different account_type_id, the
    # new value must be a seeded built-in or one of this owner's
    # custom types.  Identical to the create path -- see
    # ``_account_type_is_visible`` for the rationale.  Skip when
    # the field was not submitted (partial update).
    if (
        "account_type_id" in data
        and not _account_type_is_visible(data["account_type_id"], user_id)
    ):
        return {}, ("Invalid account type.", "danger")

    # Stale-form check.  Performed before any mutation so the audit
    # trail (AccountAnchorHistory, audit_log triggers) records only
    # successful edits.  The check is conditional on the form having
    # submitted a version (clients that omit it fall through to the
    # SQLAlchemy-tier check at flush time).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != account.version_id:
        return {}, (
            "This account was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )

    # Duplicate-name guard (if name is changing).
    if "name" in data and data["name"] != account.name:
        existing = (
            db.session.query(Account)
            .filter_by(user_id=user_id, name=data["name"])
            .first()
        )
        if existing:
            return {}, (
                "An account with that name already exists.",
                "warning",
            )

    return data, None
