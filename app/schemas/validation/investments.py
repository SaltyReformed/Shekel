"""Investment-params and investment-contribution-transfer schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_percent_fields,
)


class InvestmentContributionTransferSchema(BaseSchema):
    """Validates POST data for creating a recurring investment contribution transfer.

    The source_account_id is required (the account money comes from).
    The amount is optional -- if omitted, the route computes a suggested
    amount from the annual contribution limit and remaining pay periods.
    If provided, must be positive.
    """

    source_account_id = fields.Integer(
        required=True, validate=validate.Range(min=1),
    )
    amount = fields.Decimal(
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )


# ── Investment Schemas (Phase 5) ────────────────────────────────


class InvestmentParamsCreateSchema(BaseSchema):
    """Validates POST data for creating investment parameters.

    E-28 / HIGH-06 / F-17 (Commit 12 of the follow-up plan): the four
    percent fields below (``assumed_annual_return`` and the three
    employer-side rates) are persisted as decimal fractions matching
    the DB CHECK domains.  The ``@pre_load`` converts the form's
    user-facing percent (e.g. ``"7.5"`` for 7.5%) to its fraction
    equivalent (``"0.075"``) so the ``Range`` validator and the DB
    CHECK accept exactly the same set of values, completing the
    universal "schemas own percent conversion" convention.
    """

    _PERCENT_FIELDS = (
        "assumed_annual_return", "employer_flat_percentage",
        "employer_match_percentage", "employer_match_cap_percentage",
    )

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions."""
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    assumed_annual_return = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=-1, max=1),
    )
    # F-077 / C-24: Backstop the new DB CHECK
    # ``annual_contribution_limit IS NULL OR annual_contribution_limit >= 0``
    # with a wide form-layer ceiling.  Real limits top out at the
    # employer-plus-employee 401(k) cap (~$70K in 2026); $100M is a
    # generous typo guard.
    annual_contribution_limit = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("100000000"),
        ),
    )
    contribution_limit_year = fields.Integer(
        allow_none=True, validate=validate.Range(min=2000, max=2100),
    )
    employer_contribution_type = fields.String(
        load_default="none",
        validate=validate.OneOf(["none", "flat_percentage", "match"]),
    )
    employer_flat_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )
    employer_match_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=10),
    )
    employer_match_cap_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )


class InvestmentParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating investment parameters.

    Same fraction-domain convention as
    :class:`InvestmentParamsCreateSchema`; see that class for the
    F-17 (Commit 12) rationale.
    """

    _PERCENT_FIELDS = (
        "assumed_annual_return", "employer_flat_percentage",
        "employer_match_percentage", "employer_match_cap_percentage",
    )

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings, then convert percent fields to fractions."""
        data = {k: v for k, v in data.items() if v != ""}
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    assumed_annual_return = fields.Decimal(
        places=5, as_string=True,
        validate=validate.Range(min=-1, max=1),
    )
    # F-077 / C-24: see :class:`InvestmentParamsCreateSchema` for
    # the bound rationale; the same Range applies on update.
    annual_contribution_limit = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("100000000"),
        ),
    )
    contribution_limit_year = fields.Integer(
        allow_none=True, validate=validate.Range(min=2000, max=2100),
    )
    employer_contribution_type = fields.String(
        validate=validate.OneOf(["none", "flat_percentage", "match"]),
    )
    employer_flat_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )
    employer_match_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=10),
    )
    employer_match_cap_percentage = fields.Decimal(
        places=4, as_string=True, allow_none=True,
        validate=validate.Range(min=0, max=1),
    )
