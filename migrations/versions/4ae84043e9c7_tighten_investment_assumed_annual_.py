"""tighten investment assumed_annual_return CHECK to > -1

Replaces the ``ck_investment_params_valid_return`` CHECK on
``budget.investment_params`` from ``assumed_annual_return >= -1`` to
``assumed_annual_return > -1`` (the upper ``<= 1`` is unchanged).

A stored ``assumed_annual_return = -1`` (a -100% annual return) is a
degenerate, non-invertible assumption: the reverse growth projection
(``growth_engine.reverse_project_balance``, reached by the year-end
summary for a pre-anchor investment window) divides by
``(1 + per-period rate)``, which is exactly 0 when the rate resolves to
-1 -> ``DivisionByZero``.  The Marshmallow schemas
(``InvestmentParamsCreate/UpdateSchema.assumed_annual_return`` and the
``RetirementGapQuerySchema.return_rate`` mirror) are tightened to
``Range(min=-1, min_inclusive=False)`` in the same change; this CHECK
keeps the storage tier in lockstep with the schema (the documented
"schema and CHECK accept exactly the same set of values" invariant).

DH-#28 follow-up (deep-quality-hunt Batch S review).

Review: developer-selected Option A (DH-#28 follow-up), 2026-06-09

Revision ID: 4ae84043e9c7
Revises: 91fda897a32d
Create Date: 2026-06-09 15:25:58.913371
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = '4ae84043e9c7'
down_revision = '91fda897a32d'
branch_labels = None
depends_on = None


def upgrade():
    """Tighten the return CHECK to a strict ``> -1`` lower bound.

    Defensively clamps any pre-existing ``assumed_annual_return = -1`` row
    to ``-0.99999`` (the smallest value the ``Numeric(7, 5)`` column can
    hold above the new exclusive bound) BEFORE swapping the constraint, so
    the migration is robust even though such a row is itself the latent
    bug being closed and is not expected to exist.  ``-0.99999`` satisfies
    both the old (``>= -1``) and the new (``> -1``) predicate, so the
    clamp is valid regardless of constraint state.
    """
    op.execute(
        "UPDATE budget.investment_params "
        "SET assumed_annual_return = -0.99999 "
        "WHERE assumed_annual_return = -1"
    )
    op.drop_constraint(
        "ck_investment_params_valid_return",
        "investment_params",
        schema="budget",
        type_="check",
    )
    op.create_check_constraint(
        "ck_investment_params_valid_return",
        "investment_params",
        "assumed_annual_return > -1 AND assumed_annual_return <= 1",
        schema="budget",
    )


def downgrade():
    """Restore the inclusive ``>= -1`` lower bound.

    Widening the domain back to ``[-1, 1]`` cannot violate any row (every
    value valid under ``> -1`` is valid under ``>= -1``).  The upgrade's
    defensive ``-1 -> -0.99999`` clamp is deliberately NOT reversed: the
    original ``-1`` value was the degenerate input this migration exists
    to forbid, and it is unrecoverable from ``-0.99999`` -- restoring the
    looser constraint is sufficient to return to the pre-upgrade schema.
    """
    op.drop_constraint(
        "ck_investment_params_valid_return",
        "investment_params",
        schema="budget",
        type_="check",
    )
    op.create_check_constraint(
        "ck_investment_params_valid_return",
        "investment_params",
        "assumed_annual_return >= -1 AND assumed_annual_return <= 1",
        schema="budget",
    )
