"""
Shekel Budget App -- Reference Table Seed Data

Single source of truth for reference-table seed data and the
idempotent seeding function that materialises it.

Two exports:

* ``ACCT_TYPE_SEEDS`` -- the canonical account-type tuple list used
  to populate ``ref.account_types``.
* ``seed_reference_data(session, *, verbose=False)`` -- the idempotent
  upsert routine that populates every ref-schema table to a
  byte-identical state on every call.  Used by the application
  factory's dev/test convenience seed (``app/__init__.py``), the
  standalone production seed script (``scripts/seed_ref_tables.py``),
  the pytest fixture stack (``tests/conftest.py``), and the test
  template builder (``scripts/build_test_template.py``).

Three call sites previously duplicated this logic with subtle
divergence (one used dict-based ``REF_DATA``, another a list-based
``REF_DATA``, the third inlined the table list); a single function
eliminates the drift risk that a future migration adding a new ref
row could be applied in two call sites but not the third.

Each ``ACCT_TYPE_SEEDS`` entry: (name, category_name, has_parameters,
has_amortization, has_interest, is_pretax, is_liquid, icon_class,
max_term_months)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Typing-only imports: keep this module side-effect free at import
    # time (see the deferred ``app.models.ref`` import inside
    # ``seed_reference_data``).  ``from __future__ import annotations``
    # makes every annotation a lazy string, so neither the SQLAlchemy
    # ORM nor the model layer is imported when ``app.ref_seeds`` loads.
    from types import ModuleType

    from sqlalchemy.orm import Session

# fmt: off
# Pylint: ``line-too-long`` -- columnar alignment is intentional for
# readability: each row is one account type and the columns correspond
# to the tuple docstring above.  Wrapping individual rows harms
# scannability.
# pylint: disable=line-too-long

ACCT_TYPE_SEEDS = [
    # name              category      params amort  interest pretax liquid icon               max_term
    ("Checking",        "Asset",      False, False, False, False, True,  "bi-wallet2",        None),
    ("Savings",         "Asset",      False, False, False, False, True,  "bi-piggy-bank",     None),
    ("HYSA",            "Asset",      True,  False, True,  False, True,  "bi-piggy-bank",     None),
    ("Money Market",    "Asset",      True,  False, True,  False, True,  "bi-cash-stack",     None),
    ("CD",              "Asset",      True,  False, True,  False, False, "bi-safe",           None),
    ("HSA",             "Asset",      True,  False, True,  False, False, "bi-heart-pulse",    None),
    ("Credit Card",     "Liability",  False, False, False, False, False, "bi-credit-card",    None),
    ("Mortgage",        "Liability",  True,  True,  False, False, False, "bi-house",          600),
    ("Auto Loan",       "Liability",  True,  True,  False, False, False, "bi-car-front",      120),
    ("Student Loan",    "Liability",  True,  True,  False, False, False, "bi-mortarboard",    300),
    ("Personal Loan",   "Liability",  True,  True,  False, False, False, "bi-cash-coin",      120),
    ("HELOC",           "Liability",  True,  True,  False, False, False, "bi-bank",           360),
    ("401(k)",          "Retirement", True,  False, False, True,  False, "bi-graph-up-arrow", None),
    ("Roth 401(k)",     "Retirement", True,  False, False, False, False, "bi-graph-up-arrow", None),
    ("Traditional IRA", "Retirement", True,  False, False, True,  False, "bi-graph-up-arrow", None),
    ("Roth IRA",        "Retirement", True,  False, False, False, False, "bi-graph-up-arrow", None),
    ("Brokerage",       "Investment", True,  False, False, False, False, "bi-bar-chart-line", None),
    ("529 Plan",        "Investment", True,  False, False, False, False, "bi-mortarboard",    None),
]
# pylint: enable=line-too-long
# fmt: on


# Per-table seed data for the non-AccountType ref tables.  Entries are
# either bare strings (used as ``name``) or dicts (full row spec for
# tables with non-name columns such as ``Status``'s booleans).  The
# tuple ordering matches the existing conftest seeding order so that
# any test that asserts on stable ID assignment (none currently do,
# but the property is preserved for future safety) continues to see
# the same IDs.
#
# ``Status`` carries three boolean columns that drive runtime logic
# (settled / immutable / excludes_from_balance); copying the structure
# wholesale from the previous three call sites' dict literals is the
# canonical form -- changing any column here must be matched by a
# database migration.
# fmt: off
# Pylint: ``line-too-long`` -- the ``Status`` rows are columnar-aligned
# dict literals (one row per status, columns aligned to the boolean
# flags) for the same scannability reason as ``ACCT_TYPE_SEEDS`` above;
# wrapping individual rows harms readability.
# pylint: disable=line-too-long
_REF_TABLE_SEEDS = (
    # (model_attr_name, list of entries)
    ("TransactionType", ["Income", "Expense"]),
    ("Status", [
        {"name": "Projected", "is_settled": False, "is_immutable": False, "excludes_from_balance": False},
        {"name": "Paid",      "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
        {"name": "Received",  "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
        {"name": "Credit",    "is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
        {"name": "Cancelled", "is_settled": False, "is_immutable": True,  "excludes_from_balance": True},
        {"name": "Settled",   "is_settled": True,  "is_immutable": True,  "excludes_from_balance": False},
    ]),
    ("RecurrencePattern", [
        "Every Period", "Every N Periods", "Monthly", "Monthly First",
        "Quarterly", "Semi-Annual", "Annual", "Once",
    ]),
    ("FilingStatus", [
        "single", "married_jointly", "married_separately",
        "head_of_household",
    ]),
    ("DeductionTiming", ["pre_tax", "post_tax"]),
    ("CalcMethod", ["flat", "percentage"]),
    ("TaxType", ["flat", "none", "bracket"]),
    ("RaiseType", ["merit", "cola", "custom"]),
    ("GoalMode", ["Fixed", "Income-Relative"]),
    ("IncomeUnit", ["Paychecks", "Months"]),
    ("UserRole", ["owner", "companion"]),
    # ``LoanAnchorSource`` -- the provenance tag carried by every row
    # in ``budget.loan_anchor_events`` (CRIT-02 / E-18 / Commit 12).
    # ``origination`` is materialised once per loan from the immutable
    # LoanParams fields; ``user_trueup`` is appended by the dashboard
    # balance-edit flow (Commit 16).  Same idempotent upsert semantics
    # as the other reference tables.
    ("LoanAnchorSource", ["origination", "user_trueup"]),
    # ``EmployerContributionType`` / ``CompoundingFrequency`` (#38) --
    # the two logic-bearing enums promoted off free-string columns to
    # ref tables so the growth/interest engines branch on IDs.  Names
    # match the enum values and the prior column literals exactly, so
    # the promotion migration backfills name->id 1:1.
    ("EmployerContributionType", ["none", "flat_percentage", "match"]),
    ("CompoundingFrequency", ["daily", "monthly", "quarterly"]),
)
# pylint: enable=line-too-long
# fmt: on


_ACCT_TYPE_CATEGORY_SEEDS = ("Asset", "Liability", "Retirement", "Investment")


def seed_reference_data(session: Session, *, verbose: bool = False) -> None:
    """Idempotently populate every ref-schema lookup table.

    Runs the three-step seed:

    1. ``ref.account_type_categories`` (4 fixed rows: Asset, Liability,
       Retirement, Investment).  Must precede the AccountType seed
       because each AccountType row carries a category FK.
    2. ``ref.account_types`` from ``ACCT_TYPE_SEEDS`` (18 rows).
       Existing rows have their metadata columns UPDATED in place so
       a column-shape change in a future migration propagates
       correctly on next seed; missing rows are INSERTed.
    3. The non-AccountType ref tables from ``_REF_TABLE_SEEDS``.
       Existing rows are left alone; only missing rows are INSERTed.
       Status carries non-name columns (booleans) -- those entries
       are dicts; everything else is name-only.

    Idempotent by design: calling this twice in a row produces no
    duplicates and (modulo metadata refreshes on existing
    AccountType rows) no changes on the second call.  Safe to run
    against a fresh empty DB or one that already contains a partial
    or complete seed.

    Args:
        session: SQLAlchemy session bound to the target database.
            The function flushes between steps 1 and 2 so the
            category PKs are visible to the AccountType inserts;
            it does NOT commit -- callers own the transaction
            boundary so they can wrap the seed in their own
            commit / rollback contract (the production script
            commits; conftest commits inside ``_seed_ref_tables``;
            the template builder commits after seeding).
        verbose: When True, prints one line per inserted row.  Used
            by ``scripts/seed_ref_tables.py`` to give the operator
            an audit trail during deploy.  Default False so test
            paths run silently.

    Returns:
        None.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred import:
    # ``app.models.ref`` imports ``app.extensions`` which constructs the
    # SQLAlchemy() singleton -- importing at module load would force
    # ``app`` initialisation as a side-effect of ``import
    # app.ref_seeds``, which the test bootstrap (which needs to set
    # environment variables before app import) cannot tolerate.  The
    # deferred import keeps this module side-effect free at import time.
    # ``ref_models`` is threaded into the per-step helpers so they
    # inherit the same deferral (no module-level import).
    # pylint: disable=import-outside-toplevel
    from app.models import ref as ref_models

    _seed_account_type_categories(session, ref_models, verbose=verbose)
    # Flush so the category PKs are visible to the AccountType FK in
    # step 2.  Without this, those INSERTs would either fail with NOT
    # NULL on ``category_id`` or pick up stale IDs from a prior session.
    session.flush()
    _seed_account_types(session, ref_models, verbose=verbose)
    _seed_other_ref_tables(session, ref_models, verbose=verbose)


def _seed_account_type_categories(
    session: Session, ref_models: ModuleType, *, verbose: bool = False
) -> None:
    """Insert the 4 fixed ``AccountTypeCategory`` rows (idempotent).

    Asset / Liability / Retirement / Investment.  Existing rows are
    left untouched; only missing rows are INSERTed.  The caller must
    ``flush`` after this step so the category PKs are visible to the
    AccountType FK in :func:`_seed_account_types`.

    Args:
        session: SQLAlchemy session bound to the target database.
        ref_models: The ``app.models.ref`` module (passed in to keep
            the deferred-import discipline of the public entry point).
        verbose: When True, prints one line per inserted row.
    """
    for cat_name in _ACCT_TYPE_CATEGORY_SEEDS:
        existing = (
            session.query(ref_models.AccountTypeCategory)
            .filter_by(name=cat_name)
            .first()
        )
        if existing is None:
            session.add(ref_models.AccountTypeCategory(name=cat_name))
            if verbose:
                print(f"  + account_type_categories: {cat_name}")


def _seed_account_types(
    session: Session, ref_models: ModuleType, *, verbose: bool = False
) -> None:
    """Upsert the 18 ``AccountType`` rows from ``ACCT_TYPE_SEEDS``.

    Missing rows are INSERTed; existing rows have their metadata
    columns refreshed in place so a column-shape change in a future
    migration propagates correctly on the next seed (the canonical
    behaviour shared by the conftest, ``app/__init__.py`` and
    ``scripts/seed_ref_tables.py`` seed paths).  Requires the
    ``AccountTypeCategory`` rows to already be flushed -- their PKs back
    the ``category_id`` FK.

    Args:
        session: SQLAlchemy session bound to the target database.
        ref_models: The ``app.models.ref`` module.
        verbose: When True, prints one line per inserted row.
    """
    cat_lookup = {
        c.name: c.id
        for c in session.query(ref_models.AccountTypeCategory).all()
    }
    for (name, cat_name, has_params, has_amort,
         has_int, is_pre, is_liq, icon, max_term) in ACCT_TYPE_SEEDS:
        existing = (
            session.query(ref_models.AccountType)
            .filter_by(name=name)
            .first()
        )
        if existing is None:
            session.add(ref_models.AccountType(
                name=name,
                category_id=cat_lookup[cat_name],
                has_parameters=has_params,
                has_amortization=has_amort,
                has_interest=has_int,
                is_pretax=is_pre,
                is_liquid=is_liq,
                icon_class=icon,
                max_term_months=max_term,
            ))
            if verbose:
                print(f"  + account_types: {name}")
        else:
            existing.has_parameters = has_params
            existing.has_amortization = has_amort
            existing.has_interest = has_int
            existing.is_pretax = is_pre
            existing.is_liquid = is_liq
            existing.icon_class = icon
            existing.max_term_months = max_term


def _seed_other_ref_tables(
    session: Session, ref_models: ModuleType, *, verbose: bool = False
) -> None:
    """Insert any missing rows in the non-AccountType ref tables.

    Driven by ``_REF_TABLE_SEEDS``.  Existing rows are left untouched
    (these tables carry only ``name`` plus, for ``Status``, three
    migration-managed runtime booleans -- so there is no in-place
    metadata refresh as in step 2).  Dict entries carry the non-name
    columns (``Status``); every other entry is name-only.

    Args:
        session: SQLAlchemy session bound to the target database.
        ref_models: The ``app.models.ref`` module.
        verbose: When True, prints one line per inserted row.
    """
    for model_attr_name, entries in _REF_TABLE_SEEDS:
        model = getattr(ref_models, model_attr_name)
        for entry in entries:
            if isinstance(entry, dict):
                row_name = entry["name"]
                existing = session.query(model).filter_by(name=row_name).first()
                if existing is None:
                    session.add(model(**entry))
                    if verbose:
                        print(f"  + {model.__tablename__}: {row_name}")
            else:
                existing = session.query(model).filter_by(name=entry).first()
                if existing is None:
                    session.add(model(name=entry))
                    if verbose:
                        print(f"  + {model.__tablename__}: {entry}")
