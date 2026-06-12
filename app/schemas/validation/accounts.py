"""Account, account-type, anchor, and HYSA interest-params schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app import ref_cache
from app.enums import AcctCategoryEnum, CompoundingFrequencyEnum
from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)


def _valid_compounding_frequency_ids() -> set[int]:
    """Return the set of valid ``ref.compounding_frequencies`` IDs.

    Resolved from the cache at call time (request context) so the
    schema validates the posted FK id against the live ref table
    rather than a hardcoded id set -- the IDs-for-logic invariant (#38).
    """
    return {
        ref_cache.compounding_frequency_id(member)
        for member in CompoundingFrequencyEnum
    }


class AnchorUpdateSchema(BaseSchema):
    """Validates PATCH data for updating the account anchor balance.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the form was rendered.  The route handler compares
    the submitted value against ``Account.version_id`` and returns
    409 Conflict if they differ -- a stale-form check that catches
    the Tab-1/Tab-2 race even when the two requests are sequential
    rather than truly concurrent.  Optional so callers that have
    no way to plumb the version through (e.g. a future programmatic
    client) still pass validation; in that case only the
    SQLAlchemy ``version_id_col`` race detection applies, which
    catches the truly-concurrent case at flush time.
    """

    anchor_balance = fields.Decimal(required=True, places=2, as_string=True)
    version_id = fields.Integer(validate=validate.Range(min=1))


class AccountCreateSchema(BaseSchema):
    """Validates POST data for creating an account."""

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    account_type_id = fields.Integer(required=True)
    anchor_balance = fields.Decimal(places=2, as_string=True)


class AccountUpdateSchema(BaseSchema):
    """Validates POST data for updating an account.

    ``version_id`` is the optimistic-locking counter from the row at
    the moment the edit form was rendered.  The handler compares
    the submitted value against the current ``Account.version_id``
    and short-circuits with 409 Conflict on mismatch; see the
    matching docstring on :class:`AnchorUpdateSchema`.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    name = fields.String(validate=validate.Length(min=1, max=100))
    account_type_id = fields.Integer()
    is_active = fields.Boolean()
    anchor_balance = fields.Decimal(places=2, as_string=True)
    version_id = fields.Integer(validate=validate.Range(min=1))


class AccountTypeCreateSchema(BaseSchema):
    """Validates POST data for creating an account type.

    Includes all metadata fields that drive dispatch logic.  Cross-field
    validation ensures flag combinations are consistent with the chosen
    category (e.g. has_amortization requires Liability).
    """

    name = fields.String(required=True, validate=validate.Length(min=1, max=30))
    category_id = fields.Integer(required=True)
    has_parameters = fields.Boolean(load_default=False)
    has_amortization = fields.Boolean(load_default=False)
    has_interest = fields.Boolean(load_default=False)
    is_pretax = fields.Boolean(load_default=False)
    is_liquid = fields.Boolean(load_default=False)
    icon_class = fields.String(
        load_default="bi-bank",
        validate=validate.Length(max=30),
    )
    max_term_months = fields.Integer(
        load_default=None, allow_none=True,
        validate=validate.Range(min=1, max=600),
    )

    @validates_schema
    def validate_flag_combinations(self, data, **kwargs):
        """Enforce category-flag consistency rules."""

        cat_id = data.get("category_id")
        liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
        asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
        retirement_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)

        if data.get("has_amortization") and cat_id != liability_id:
            raise ValidationError(
                "has_amortization requires Liability category.",
                field_name="has_amortization",
            )
        if data.get("has_interest") and cat_id != asset_id:
            raise ValidationError(
                "has_interest requires Asset category.",
                field_name="has_interest",
            )
        if data.get("is_pretax") and cat_id != retirement_id:
            raise ValidationError(
                "is_pretax requires Retirement category.",
                field_name="is_pretax",
            )
        if data.get("is_liquid") and cat_id != asset_id:
            raise ValidationError(
                "is_liquid requires Asset category.",
                field_name="is_liquid",
            )
        if data.get("has_amortization") and data.get("has_interest"):
            raise ValidationError(
                "has_amortization and has_interest are mutually exclusive.",
                field_name="has_amortization",
            )
        if data.get("max_term_months") and not data.get("has_amortization"):
            raise ValidationError(
                "max_term_months requires has_amortization.",
                field_name="max_term_months",
            )


class AccountTypeUpdateSchema(BaseSchema):
    """Validates POST data for updating an account type.

    All fields are optional for partial updates.  Cross-field
    validation mirrors AccountTypeCreateSchema but only fires when
    the relevant fields are present in the submitted data.
    """

    name = fields.String(validate=validate.Length(min=1, max=30))
    category_id = fields.Integer()
    has_parameters = fields.Boolean()
    has_amortization = fields.Boolean()
    has_interest = fields.Boolean()
    is_pretax = fields.Boolean()
    is_liquid = fields.Boolean()
    icon_class = fields.String(validate=validate.Length(max=30))
    max_term_months = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1, max=600),
    )

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None.

        When *data* is a Werkzeug MultiDict (HTML form submission), take
        the last value for each key so the hidden-input + checkbox pattern
        resolves correctly: checked -> 'true' (last value wins), unchecked
        -> 'false' (sole value from hidden input).  The flattened dict
        then goes through the shared empty-input rule so the nullable
        ``max_term_months`` clears on an empty submit.
        """
        if hasattr(data, "getlist"):
            data = {
                k: vs[-1] for k in data if (vs := data.getlist(k))
            }
        return _normalize_empty_inputs(self, data)

    @validates_schema
    def validate_flag_combinations(self, data, **kwargs):
        """Enforce category-flag consistency rules on partial updates.

        Category-flag checks only fire when both the flag and
        category_id are present, so partial updates that omit
        category_id do not falsely reject.  Mutual-exclusion and
        dependency checks fire whenever both relevant fields are
        present.
        """

        cat_id = data.get("category_id")

        if cat_id is not None:
            liability_id = ref_cache.acct_category_id(AcctCategoryEnum.LIABILITY)
            asset_id = ref_cache.acct_category_id(AcctCategoryEnum.ASSET)
            retirement_id = ref_cache.acct_category_id(AcctCategoryEnum.RETIREMENT)

            if data.get("has_amortization") and cat_id != liability_id:
                raise ValidationError(
                    "has_amortization requires Liability category.",
                    field_name="has_amortization",
                )
            if data.get("has_interest") and cat_id != asset_id:
                raise ValidationError(
                    "has_interest requires Asset category.",
                    field_name="has_interest",
                )
            if data.get("is_pretax") and cat_id != retirement_id:
                raise ValidationError(
                    "is_pretax requires Retirement category.",
                    field_name="is_pretax",
                )
            if data.get("is_liquid") and cat_id != asset_id:
                raise ValidationError(
                    "is_liquid requires Asset category.",
                    field_name="is_liquid",
                )

        if data.get("has_amortization") and data.get("has_interest"):
            raise ValidationError(
                "has_amortization and has_interest are mutually exclusive.",
                field_name="has_amortization",
            )
        if data.get("max_term_months") and not data.get("has_amortization"):
            raise ValidationError(
                "max_term_months requires has_amortization.",
                field_name="max_term_months",
            )


# ── HYSA Schemas ──────────────────────────────────────────────────


class InterestParamsCreateSchema(BaseSchema):
    """Validates POST data for creating interest parameters.

    E-28 / HIGH-06 / PA-02: ``apy`` is validated as a decimal fraction
    in ``[0, 1]`` matching the DB ``CHECK(apy >= 0 AND apy <= 1)``.
    The ``@pre_load`` divides the form's user-facing percent (e.g.
    ``"4.5"``) by 100 so the ``Range`` validator and the DB CHECK
    accept exactly the same set of values.
    """

    _PERCENT_FIELDS = ("apy",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    # apy storage is a fraction; see class docstring.  ``places=5``
    # mirrors the column's ``Numeric(7, 5)`` precision so a 4.5%
    # input (``"4.5"`` -> fraction ``"0.04500"``) round-trips
    # without loss.
    apy = fields.Decimal(
        required=True, places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    # #38: compounding frequency is now a ref-table FK id, not a free
    # string.  Required on create, mirroring the prior String field.
    compounding_frequency_id = fields.Integer(required=True)

    @validates_schema
    def validate_compounding_frequency(self, data, **kwargs):
        """Reject a compounding-frequency id outside the ref table."""
        freq_id = data.get("compounding_frequency_id")
        if freq_id is not None and (
            freq_id not in _valid_compounding_frequency_ids()
        ):
            raise ValidationError(
                "Invalid compounding frequency.",
                field_name="compounding_frequency_id",
            )


class InterestParamsUpdateSchema(BaseSchema):
    """Validates POST data for updating interest parameters.

    Same fraction-domain convention as :class:`InterestParamsCreateSchema`.
    ``apy`` is optional here so a partial update (e.g. compounding
    frequency only) does not have to resubmit the rate.
    """

    _PERCENT_FIELDS = ("apy",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions."""
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    apy = fields.Decimal(
        places=5, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    # #38: ref-table FK id (see :class:`InterestParamsCreateSchema`).
    # Optional on update so a partial payload (e.g. apy only) need not
    # resubmit the frequency.
    compounding_frequency_id = fields.Integer()

    @validates_schema
    def validate_compounding_frequency(self, data, **kwargs):
        """Reject a compounding-frequency id outside the ref table.

        Only fires when present -- a partial update that omits it
        leaves the stored frequency unchanged.
        """
        freq_id = data.get("compounding_frequency_id")
        if freq_id is not None and (
            freq_id not in _valid_compounding_frequency_ids()
        ):
            raise ValidationError(
                "Invalid compounding frequency.",
                field_name="compounding_frequency_id",
            )
