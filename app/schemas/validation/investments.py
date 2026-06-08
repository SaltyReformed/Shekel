"""Investment-params and investment-contribution-transfer schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    post_load,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app import ref_cache
from app.enums import EmployerContributionTypeEnum
from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_percent_fields,
)


def _valid_employer_contribution_type_ids() -> set[int]:
    """Return the set of valid ``ref.employer_contribution_types`` IDs.

    Resolved from the cache at call time (request context) so the
    schema validates the posted FK id against the live ref table
    rather than a hardcoded id set -- the IDs-for-logic invariant (#38).
    """
    return {
        ref_cache.employer_contribution_type_id(member)
        for member in EmployerContributionTypeEnum
    }


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
    # #38: the employer-contribution type is now a ref-table FK id, not
    # a free string.  Omitted (no row yet, or a JSON caller that leaves
    # it off) defaults to NONE in ``default_employer_contribution_type``
    # below -- the faithful successor to the prior ``load_default="none"``.
    employer_contribution_type_id = fields.Integer(
        load_default=None, allow_none=True,
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

    @validates_schema
    def validate_employer_contribution_type(self, data, **kwargs):
        """Reject an employer-contribution-type id outside the ref table."""
        type_id = data.get("employer_contribution_type_id")
        if type_id is not None and (
            type_id not in _valid_employer_contribution_type_ids()
        ):
            raise ValidationError(
                "Invalid employer contribution type.",
                field_name="employer_contribution_type_id",
            )

    @post_load
    def default_employer_contribution_type(self, data, **kwargs):
        """Default a missing employer-contribution type to NONE.

        Preserves the prior schema's ``load_default="none"`` semantics
        now that the column is an FK id: a create that omits the field
        lands on the seeded NONE row rather than a NULL FK.
        """
        if data.get("employer_contribution_type_id") is None:
            data["employer_contribution_type_id"] = (
                ref_cache.employer_contribution_type_id(
                    EmployerContributionTypeEnum.NONE,
                )
            )
        return data


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
    # #38: ref-table FK id (see :class:`InvestmentParamsCreateSchema`).
    # No default on update -- a partial payload that omits it leaves the
    # stored type unchanged.
    employer_contribution_type_id = fields.Integer()
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

    @validates_schema
    def validate_employer_contribution_type(self, data, **kwargs):
        """Reject an employer-contribution-type id outside the ref table.

        Only fires when the field is present -- a partial update that
        omits it skips the check and leaves the stored type unchanged.
        """
        type_id = data.get("employer_contribution_type_id")
        if type_id is not None and (
            type_id not in _valid_employer_contribution_type_ids()
        ):
            raise ValidationError(
                "Invalid employer contribution type.",
                field_name="employer_contribution_type_id",
            )
