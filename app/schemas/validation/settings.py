"""User-settings validation schema."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
)

from app.schemas.validation._helpers import (
    BaseSchema,
    RowId,
    _normalize_empty_inputs,
    _normalize_percent_fields,
)


class UserSettingsSchema(BaseSchema):
    """Validates POST data for updating user settings.

    E-28 / HIGH-06 / PA-02: ``default_inflation_rate`` is persisted as a
    decimal fraction (DB CHECK ``[0, 1]``).  The ``@pre_load`` converts
    the percent input to its fraction equivalent so the schema's
    ``Range`` validator and the DB CHECK agree on the accepted set.
    (``trend_alert_threshold``, the PA-01 sibling this hook once also
    converted, was removed with the retired spending trend engine.)
    """

    _PERCENT_FIELDS = ("default_inflation_rate",)

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Normalize empty inputs, then convert percent fields to fractions.

        The grid-account "clear" carve-out this hook used to hand-roll
        is now the general rule: ``_normalize_empty_inputs`` maps an
        empty ``allow_none`` field (here ``default_grid_account_id``)
        to ``None`` instead of dropping it.
        """
        data = _normalize_empty_inputs(self, data)
        return _normalize_percent_fields(data, self._PERCENT_FIELDS)

    grid_default_periods = fields.Integer(
        validate=validate.Range(min=1, max=52),
    )
    default_inflation_rate = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    low_balance_threshold = fields.Integer(
        validate=validate.Range(min=0),
    )
    large_transaction_threshold = fields.Integer(
        validate=validate.Range(min=0),
    )
    anchor_staleness_days = fields.Integer(
        validate=validate.Range(min=1),
    )
    default_grid_account_id = RowId(allow_none=True)
