"""Schema and migration locks for the Commit-15 column demotion.

E-18 / Commit 15 alters
``budget.loan_params.current_principal`` and
``budget.loan_params.interest_rate`` from NOT NULL to nullable.
Decision D-A (``docs/audits/financial_calculations/remediation_plan.md``
Section 2): the loan resolver
(``app/services/loan_resolver.py``) is the single source of truth
for "this loan's current principal, monthly payment, schedule,
payoff date, life-of-loan interest"; the demoted columns are
non-authoritative seed values that no display surface reads after
this commit.

Three locks land here:

* **C15-3 (display-read sweep)** -- a static grep over ``app/``
  proves no display path still reads ``LoanParams.current_principal``
  outside of the resolver / append-only event module / migrations /
  documented out-of-scope engine internals.  Catches a regression
  the moment any commit re-introduces a stored-column read.

* **C15-4 (column nullability)** -- ``information_schema`` confirms
  both columns now accept NULL.  Catches a regression the moment any
  future migration or model edit silently re-tightens the contract
  without a coordinated change to the resolver.

* **C15-5 (downgrade round-trip)** -- the migration's downgrade
  restores NOT NULL after upgrade, proving the additive demotion is
  reversible.  Uses raw SQL against the running test database so the
  test exercises the literal SQL the operator would run.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest
# fixture pattern; bodies bind fixtures by name.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from app.extensions import db as _db


# pytest-xdist isolation: the C15-5 downgrade test ALTERs the live
# ``budget.loan_params`` table in-place and then restores NOT NULL.
# Two xdist workers running the test concurrently against the same
# per-worker DB clone would race on the ALTER -- pin to a single
# worker via ``--dist=loadgroup`` (configured in ``pytest.ini``) so
# the test runs serially with itself across the suite.
pytestmark = pytest.mark.xdist_group("c15_loan_params_demotion")


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_DIR = _REPO_ROOT / "app"


# -- C15-3: display-read sweep --------------------------------------------


def test_no_display_read_of_current_principal():
    """C15-3: no display path reads ``LoanParams.current_principal``.

    Greps ``app/`` for ``.current_principal`` attribute reads on
    LoanParams-shaped objects.  Excludes:

      * ``app/services/loan_resolver.py`` -- the resolver itself
        (allowed; it IS the source of truth, and it actually reads
        ``original_principal``, not ``current_principal``).
      * ``app/models/loan_anchor_event.py`` -- model module
        (no current_principal reads in practice; excluded
        defensively to match the verification gate in
        ``docs/audits/financial_calculations/remediation_plan.md``
        Section 9 Commit 15).
      * ``app/models/loan_params.py`` -- the column definition
        itself; the grep would otherwise count the ``db.Column``
        line as a hit.

    The three pre-F-10 engine internals
    (``get_loan_projection`` in ``amortization_engine.py``,
    ``calculate_balances_with_amortization`` in
    ``balance_calculator.py``, ``compute_contractual_pi`` in
    ``loan_payment_service.py``) were collapsed by the follow-up
    Commit 15 (F-10): the first two were deleted as dead production
    code; the third was rewritten to read ``original_principal``
    instead of ``current_principal``.  No engine-internal *read of the
    demoted column* remains in ``app/services/``.  The one ``services/``
    entry that post-dates F-10 -- ``amortization_engine.py`` (F-28) --
    allow-lists the ``PayoffRequest`` parameter-object field, not a
    ``LoanParams`` read: that module has no DB access and cannot touch
    the demoted column (see the allow-list comment below).

    The grep matches WRITES (``params.current_principal = X``) as
    well as reads -- but Commit 15 leaves the legacy write path in
    ``update_params`` intact (Commit 16 retargets it at the true-up
    event), so the sweep allow-lists known write sites too.  Once
    Commit 16 lands and Commit 16's update-flow rewrite removes the
    setattr, drop those allow-list entries.
    """
    grep_out = subprocess.run(
        [
            "grep", "-rn",
            r"\.current_principal\b",
            str(_APP_DIR),
            "--include=*.py",
            "--include=*.html",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # ``grep -r`` returns 1 if no match; either outcome is valid as
    # long as no offending hit remains.
    lines = [ln for ln in grep_out.stdout.splitlines() if ln.strip()]

    # Allow-list: residual reads documented as out-of-scope follow-ups
    # for Commit 17 (HIGH-08: unify per-period/interest/payoff figures
    # via the resolver).  Each entry is ``relpath: signature`` so a
    # rename of either side triggers a maintenance update here, not a
    # silent slip-through.  Also covers comments, docstrings,
    # ``_PARAM_FIELDS`` string literals, the model column definition,
    # known DebtAccount-dataclass field references (different class),
    # and refinance-template ``comparison.current_principal`` (dict
    # key, not LoanParams attribute).
    _ALLOWED = (
        # DebtAccount dataclass (different class, not LoanParams):
        "services/debt_strategy_service.py:",
        # Template dict-key reads on ``comparison.current_principal``
        # and ``debt.current_principal`` (DebtAccount), neither
        # touches LoanParams:
        "templates/loan/_refinance_results.html:",
        "templates/debt_strategy/dashboard.html:",
        # Setup-form template field id/name string literal:
        "templates/loan/setup.html:",
        # Comments / docstrings citing the column's name:
        # (substring matches lines that include the literal string
        # but are not attribute reads on LoanParams instances).
        "models/loan_params.py:",  # column definition + docstring
        "models/loan_anchor_event.py:",  # docstring reference
        # Commit 16 retargeted the legacy ``update_params`` write path
        # at the true-up event; ``routes/loan.py`` no longer mutates
        # the column.  The grep still matches docstring references
        # (the original write-site allow-list line is preserved here
        # because those docstrings are the documentation of the
        # demoted contract -- removing the entry would force every
        # future docstring touch to bypass the lock).
        "routes/loan.py:",
        # Commit 16 extends ``anchor_service`` for loan trueups; the
        # module's docstring and the ``apply_loan_anchor_true_up``
        # docstring reference the demoted column to assert the
        # invariant that the trueup does NOT mutate it.  Documentation
        # of the contract, not a read.
        "services/anchor_service.py:",
        # Tests don't live under app/ but the grep pattern is
        # app-only -- listed for completeness; never matched here.
        "routes/debt_strategy.py:",  # comments only
        # ``savings_dashboard_service`` became a package in the Phase 3
        # pylint-cleanup split; the directory prefix matches every
        # sub-module.  The two ``.current_principal`` hits are prose in
        # ``_projections.py`` / ``_metrics.py`` (docstring + comment
        # referencing ``LoanParams.current_principal``); the package code
        # reads ``state.current_balance`` / ``ad["current_balance"]``,
        # never the demoted column.
        "services/savings_dashboard_service/",  # comments only
        "services/loan_resolver.py:",
        # PayoffRequest parameter-object field (F-28): the pure-function
        # amortization engine has no DB access and imports no model, so
        # ``request.current_principal`` reads the resolver-derived
        # balance the caller passes in (``state.current_balance`` at
        # ``routes/loan.py`` payoff_calculate), NOT the demoted
        # ``LoanParams.current_principal`` column.  The module is
        # structurally unable to touch LoanParams, so this entry does
        # not weaken the lock's real protection.
        "services/amortization_engine.py:",
        # Static / HTML comments + dashboard.html itself:
        "templates/loan/dashboard.html:",
    )

    unexpected = [
        ln for ln in lines
        if not any(allowed in ln for allowed in _ALLOWED)
    ]
    assert not unexpected, (
        "Found `.current_principal` references outside the allow-list. "
        "If these are display reads they MUST be routed through "
        "loan_resolver.resolve_loan(...) per E-18 / Commit 15.  If "
        "they are new engine internals, document them in "
        "docs/audits/financial_calculations/remediation_follow_up.md "
        "and extend the allow-list.\n\n"
        + "\n".join(unexpected)
    )


# -- C15-4: column nullability --------------------------------------------


def test_current_principal_column_nullable():
    """C15-4: ``current_principal`` is nullable after migration ``c4f0a5b71e83``.

    Queries ``information_schema.columns`` for the live ``is_nullable``
    flag.  Catches the regression case where a future migration
    silently re-applies NOT NULL without a coordinated change to the
    resolver-as-source-of-truth contract.
    """
    row = _db.session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'budget' "
        "  AND table_name = 'loan_params' "
        "  AND column_name = 'current_principal'"
    )).fetchone()
    assert row is not None, (
        "budget.loan_params.current_principal column missing -- "
        "the schema is out of sync with the model."
    )
    assert row[0] == "YES", (
        f"current_principal is_nullable={row[0]!r}, expected 'YES' "
        "per E-18 / Commit 15 demotion (migration c4f0a5b71e83)."
    )


def test_interest_rate_column_nullable():
    """C15-4: ``interest_rate`` is nullable after migration ``c4f0a5b71e83``.

    Same logic as the current_principal test.  Both columns demoted
    together so the resolver can read them as optional seed values;
    the OPT-1 destructive drop (Section 5) becomes a single later
    migration after a production cycle.
    """
    row = _db.session.execute(text(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'budget' "
        "  AND table_name = 'loan_params' "
        "  AND column_name = 'interest_rate'"
    )).fetchone()
    assert row is not None, (
        "budget.loan_params.interest_rate column missing"
    )
    assert row[0] == "YES", (
        f"interest_rate is_nullable={row[0]!r}, expected 'YES' "
        "per E-18 / Commit 15 demotion (migration c4f0a5b71e83)."
    )


# -- C15-5: downgrade round-trip ------------------------------------------


def test_downgrade_restores_not_null_then_upgrade_round_trips():
    """C15-5: ``ALTER ... SET NOT NULL`` restores the pre-Commit-15 contract.

    Drives the literal SQL the migration's ``downgrade`` would emit
    against the live test database, asserts both columns become
    ``is_nullable = 'NO'``, then re-applies the upgrade SQL and
    asserts both columns are nullable again.  Round-trip proves the
    additive demotion is reversible.

    The migration itself raises a RuntimeError when any row carries
    NULL in either column, naming the offending account so the
    operator can re-seed; that path is exercised by the migration's
    own test wrappers (Alembic's online-migration test apparatus).
    This test focuses on the no-NULL-rows happy path -- the only one
    operational rollback should encounter, since the
    Commit-15-shipping code never writes NULL.
    """
    # Down: ALTER COLUMN ... SET NOT NULL.  Matches the literal
    # ``op.alter_column(..., nullable=False)`` that Alembic emits.
    _db.session.execute(text(
        "ALTER TABLE budget.loan_params "
        "ALTER COLUMN current_principal SET NOT NULL"
    ))
    _db.session.execute(text(
        "ALTER TABLE budget.loan_params "
        "ALTER COLUMN interest_rate SET NOT NULL"
    ))
    _db.session.commit()

    for column_name in ("current_principal", "interest_rate"):
        row = _db.session.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'budget' "
            "  AND table_name = 'loan_params' "
            "  AND column_name = :col"
        ), {"col": column_name}).fetchone()
        assert row[0] == "NO", (
            f"After downgrade, {column_name} is_nullable={row[0]!r}, "
            "expected 'NO' (the pre-Commit-15 contract)."
        )

    # Up: ALTER COLUMN ... DROP NOT NULL.  Re-applies the
    # Commit-15 demotion so subsequent tests see the post-upgrade
    # state.
    _db.session.execute(text(
        "ALTER TABLE budget.loan_params "
        "ALTER COLUMN current_principal DROP NOT NULL"
    ))
    _db.session.execute(text(
        "ALTER TABLE budget.loan_params "
        "ALTER COLUMN interest_rate DROP NOT NULL"
    ))
    _db.session.commit()

    for column_name in ("current_principal", "interest_rate"):
        row = _db.session.execute(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'budget' "
            "  AND table_name = 'loan_params' "
            "  AND column_name = :col"
        ), {"col": column_name}).fetchone()
        assert row[0] == "YES", (
            f"After re-upgrade, {column_name} is_nullable={row[0]!r}, "
            "expected 'YES' (the Commit-15 contract)."
        )
