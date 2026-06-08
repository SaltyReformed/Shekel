"""User-settings validation schema."""


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


class UserSettingsSchema(BaseSchema):
    """Validates POST data for updating user settings.

    E-28 / HIGH-06 / PA-01 / PA-02: ``default_inflation_rate`` and
    ``trend_alert_threshold`` are persisted as decimal fractions
    (DB CHECK ``[0, 1]`` on both columns).  The ``@pre_load`` converts
    each percent input to its fraction equivalent so the schema's
    ``Range`` validator and the DB CHECK agree on the accepted set.
    Pre-Commit-24 the schema declared ``trend_alert_threshold`` as
    ``Integer Range(1..100)`` while the DB CHECK admitted ``[0, 1]``;
    only the literal value ``1`` satisfied both domains nominally
    (the route's ``/100`` reconciled it in practice, but the schema
    layer rejected ``0`` which is now a valid "alert disabled"
    state under E-12 "zero is a value").
    """

    _PERCENT_FIELDS = ("default_inflation_rate", "trend_alert_threshold")

    @pre_load
    def normalize_inputs(self, data, **kwargs):
        """Strip empty strings (with the grid-account "clear" carve-out),
        then convert percent fields to fractions."""
        cleaned = {}
        for k, v in data.items():
            if k == "default_grid_account_id" and v == "":
                cleaned[k] = None  # Empty string means "clear".
            elif v != "":
                cleaned[k] = v
        return _normalize_percent_fields(cleaned, self._PERCENT_FIELDS)

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
    # E-28 / PA-01: validated as a fraction; the route stores the
    # ``Decimal`` directly into the ``Numeric(5, 4)`` column.  Zero is
    # now a valid "alert disabled" state per E-12.
    trend_alert_threshold = fields.Decimal(
        places=4, as_string=True,
        validate=validate.Range(min=Decimal("0"), max=Decimal("1")),
    )
    anchor_staleness_days = fields.Integer(
        validate=validate.Range(min=1),
    )
    default_grid_account_id = fields.Integer(allow_none=True)
