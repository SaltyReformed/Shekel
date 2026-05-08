"""C-25 schema boundary-inclusivity tests.

Closes F-106, F-107, and F-135 of the 2026-04-15 security remediation
plan.  Three schemas had ``Range(min=0)`` validators that accepted 0
even though the corresponding database CHECK constraint was strictly
positive (``> 0``).  The gap shifted the rejection from a clean
field-level 400 (Marshmallow validation) to a 500
``IntegrityError`` (PostgreSQL constraint violation).  C-25 tightens
each schema to ``Range(min=Decimal("0"), min_inclusive=False)`` (or
the equivalent ``min=Decimal("0.01")`` form already in use in some
schemas) so the schema and storage tiers agree on the boundary.

Each test asserts both halves of the contract:

  * The previously-accepted-but-DB-violating value (0) is rejected
    with a field-level ``ValidationError``.
  * A legitimate strictly-positive value still loads cleanly.

Reference: F-106 + F-107 + F-135 / commit C-25 of the 2026-04-15
security remediation plan.
"""
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.schemas.validation import (
    DeductionCreateSchema,
    LoanParamsCreateSchema,
    SavingsGoalCreateSchema,
    SavingsGoalUpdateSchema,
)


# ---------------------------------------------------------------------------
# F-106 -- savings_goals.contribution_per_period
# ---------------------------------------------------------------------------
#
# DB CHECK: ``contribution_per_period IS NULL OR
#            contribution_per_period > 0``.
# Pre-C-25 schema: ``Range(min=0)`` -- accepted 0.  Submitted 0 would
# have surfaced as a 500 IntegrityError on commit.


class TestSavingsGoalContributionPerPeriodCreate:
    """Boundary inclusivity tests for ``SavingsGoalCreateSchema``."""

    def test_zero_contribution_rejected(self):
        """contribution_per_period=0 fails Range(min=0, min_inclusive=False).

        Pre-C-25 the schema accepted 0 here and the DB CHECK rejected
        it on commit, producing a 500.  Post-C-25 the schema rejects
        it cleanly with a field-level error message.
        """
        with pytest.raises(ValidationError) as exc:
            SavingsGoalCreateSchema().load({
                "account_id": "1",
                "name": "Zero Contribution",
                "target_amount": "1000.00",
                "contribution_per_period": "0",
            })
        assert "contribution_per_period" in exc.value.messages

    def test_strictly_positive_contribution_accepted(self):
        """A strictly positive contribution loads as Decimal."""
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Positive Contribution",
            "target_amount": "1000.00",
            "contribution_per_period": "0.01",
        })
        assert data["contribution_per_period"] == Decimal("0.01")

    def test_explicit_none_accepted(self):
        """``contribution_per_period=None`` is allowed (column is nullable).

        F-106 / C-25 added ``allow_none=True`` so a JSON caller can
        clear the contribution explicitly.  The form path is already
        covered by the ``strip_empty_strings`` @pre_load.
        """
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Null Contribution",
            "target_amount": "1000.00",
            "contribution_per_period": None,
        })
        assert data["contribution_per_period"] is None

    def test_omitted_contribution_loads_default_none(self):
        """Omitting ``contribution_per_period`` resolves to ``None``.

        ``load_default=None`` ensures the deserialised dict carries
        the explicit None sentinel rather than being missing -- the
        savings-goal route relies on a present-but-None value to
        distinguish "no contribution rule" from "missing field".
        """
        data = SavingsGoalCreateSchema().load({
            "account_id": "1",
            "name": "Missing Contribution",
            "target_amount": "1000.00",
        })
        assert data["contribution_per_period"] is None


class TestSavingsGoalContributionPerPeriodUpdate:
    """Boundary inclusivity tests for ``SavingsGoalUpdateSchema``."""

    def test_zero_contribution_rejected(self):
        """Update path also rejects contribution_per_period=0."""
        with pytest.raises(ValidationError) as exc:
            SavingsGoalUpdateSchema().load({
                "contribution_per_period": "0",
            })
        assert "contribution_per_period" in exc.value.messages

    def test_strictly_positive_contribution_accepted(self):
        """Strictly positive update loads cleanly."""
        data = SavingsGoalUpdateSchema().load({
            "contribution_per_period": "12.34",
        })
        assert data["contribution_per_period"] == Decimal("12.34")

    def test_explicit_none_clears_contribution(self):
        """Update path accepts None to clear the contribution."""
        data = SavingsGoalUpdateSchema().load({
            "contribution_per_period": None,
        })
        assert data["contribution_per_period"] is None


# ---------------------------------------------------------------------------
# F-107 -- loan_params.original_principal
# ---------------------------------------------------------------------------
#
# DB CHECK: ``original_principal > 0``.
# Pre-C-25 schema: ``Range(min=0)`` -- accepted 0.


class TestLoanParamsOriginalPrincipal:
    """Boundary inclusivity tests for ``LoanParamsCreateSchema``."""

    def _payload(self, original_principal):
        """Return a complete create payload with the given principal.

        Loan params has several required fields -- this helper keeps
        each test focused on the single boundary value under
        examination.  All non-principal fields use realistic values
        so the assertion isolates the principal validator alone.
        """
        return {
            "original_principal": original_principal,
            "current_principal": "100000.00",
            "interest_rate": "5.50000",
            "term_months": "360",
            "origination_date": "2020-01-01",
            "payment_day": "1",
        }

    def test_zero_principal_rejected(self):
        """original_principal=0 fails Range(min=0, min_inclusive=False)."""
        with pytest.raises(ValidationError) as exc:
            LoanParamsCreateSchema().load(self._payload("0"))
        assert "original_principal" in exc.value.messages

    def test_strictly_positive_principal_accepted(self):
        """A strictly positive principal loads as Decimal."""
        data = LoanParamsCreateSchema().load(
            self._payload("250000.00"),
        )
        assert data["original_principal"] == Decimal("250000.00")

    def test_negative_principal_rejected(self):
        """Negative principal still fails (regression check on the bound)."""
        with pytest.raises(ValidationError) as exc:
            LoanParamsCreateSchema().load(self._payload("-1.00"))
        assert "original_principal" in exc.value.messages


# ---------------------------------------------------------------------------
# F-135 -- paycheck_deductions.annual_cap
# ---------------------------------------------------------------------------
#
# DB CHECK: ``annual_cap IS NULL OR annual_cap > 0``.
# Pre-C-25 schema: ``Range(min=Decimal("0.01"), max=...)`` -- functionally
# identical to ``min=0, min_inclusive=False`` for a Decimal(places=2)
# field.  C-25 rewrites it to the explicit ``min_inclusive=False`` form
# to match the rest of the file's idiom for strictly-positive monetary
# fields.


class TestPaycheckDeductionAnnualCap:
    """Boundary inclusivity tests for ``DeductionCreateSchema.annual_cap``."""

    def _payload(self, annual_cap=None):
        """Return a complete create payload.

        Builds a flat-dollar Roth-style deduction so the
        ``calc_method PERCENTAGE`` cross-field rule does not fire and
        narrow the amount bound; the test focuses on annual_cap.
        ``calc_method_id``/``deduction_timing_id`` use placeholder
        FK-eligible values -- the schema does not validate FK
        existence at load time, so any positive integer is accepted.
        """
        payload = {
            "name": "Roth IRA",
            "amount": "500.0000",
            "deductions_per_year": "26",
            "calc_method_id": "999",
            "deduction_timing_id": "999",
        }
        if annual_cap is not None:
            payload["annual_cap"] = annual_cap
        return payload

    def test_zero_annual_cap_rejected(self):
        """annual_cap=0 fails the strictly-positive Range bound.

        DB CHECK is ``annual_cap IS NULL OR annual_cap > 0``; 0
        violates it but a NULL is a legal "no cap" sentinel.
        """
        with pytest.raises(ValidationError) as exc:
            DeductionCreateSchema().load(self._payload("0"))
        assert "annual_cap" in exc.value.messages

    def test_strictly_positive_annual_cap_accepted(self):
        """A strictly positive cap loads as Decimal."""
        data = DeductionCreateSchema().load(
            self._payload("23000.00"),
        )
        assert data["annual_cap"] == Decimal("23000.00")

    def test_omitted_annual_cap_is_uncapped(self):
        """Omitting annual_cap yields no key in the loaded dict.

        The route layer treats absent annual_cap as "no cap"; the
        schema does not synthesise a default sentinel.
        """
        data = DeductionCreateSchema().load(self._payload())
        assert "annual_cap" not in data
