"""Debt-strategy calculator validation schema."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app.services.debt_strategy_service import (
    STRATEGY_AVALANCHE,
    STRATEGY_CUSTOM,
    STRATEGY_SNOWBALL,
)
from app.schemas.validation._helpers import (
    BaseSchema,
    _normalize_empty_inputs,
)


class DebtStrategyCalculateSchema(BaseSchema):
    """Validates POST data for the debt strategy calculator.

    ``extra_monthly`` is the additional monthly payment the user
    proposes to allocate across debts; the field-level ``Range``
    rejects negative values at the schema tier and the upper bound
    rejects a typo that injected an extra digit before the
    ``calculate_strategy`` service amplifies it through the payoff
    simulation.  ``Decimal("1000000")`` is far above any realistic
    debt-payoff budget but well within the simulation's float-free
    arithmetic range, so an order-of-magnitude typo on a routine
    entry is rejected here rather than producing wildly skewed
    payoff timelines.

    ``strategy`` must be one of the three constants exported by
    ``app.services.debt_strategy_service`` (``avalanche``,
    ``snowball``, ``custom``); the ``OneOf`` validator surfaces the
    same "Invalid strategy" message the route used to compose by
    hand.  ``load_default`` mirrors the form's pre-selected radio
    button so a payload that legitimately omits the field still
    deserialises -- a defensive choice against a future caller that
    drops the field while keeping the existing UX for the HTML
    form.

    ``custom_order`` is the priority list for the ``custom``
    strategy.  The schema validates the raw string (presence and
    length); the route splits on commas, coerces to integers, and
    runs the IDOR cross-account-ownership check that the schema
    cannot do without a database session.  The 500-character cap is
    generous -- each debt account ID is at most ~10 digits plus a
    comma, so 500 characters fits roughly 40 accounts while
    rejecting a pathological payload.  ``custom`` requires the
    field; the cross-field rule
    :meth:`validate_custom_requires_order` enforces that contract
    so a future JSON caller gets the same error shape as the HTML
    form.

    Audit references: F-040 / commit C-27 of the 2026-04-15 security
    remediation plan.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Drop empty inputs; map empties on nullable fields to None."""
        return _normalize_empty_inputs(self, data)

    extra_monthly = fields.Decimal(
        load_default=Decimal("0"), places=2, as_string=True,
        validate=validate.Range(
            min=Decimal("0"), max=Decimal("1000000"),
        ),
    )
    strategy = fields.String(
        load_default=STRATEGY_AVALANCHE,
        validate=validate.OneOf(
            (STRATEGY_AVALANCHE, STRATEGY_SNOWBALL, STRATEGY_CUSTOM),
        ),
    )
    custom_order = fields.String(
        load_default=None, allow_none=True,
        validate=validate.Length(min=1, max=500),
    )

    @validates_schema
    def validate_custom_requires_order(self, data, **kwargs):
        """Require ``custom_order`` when ``strategy == 'custom'``.

        Mirrors the cross-field rule the route enforced inline
        before commit C-27.  Keeping the rule on the schema means a
        future caller (a JSON client, a CLI script) gets the same
        error shape as the HTML form path -- and the message text
        matches the legacy route output so the existing
        ``test_custom_missing_order`` UX assertion still holds.
        """
        if data.get("strategy") != STRATEGY_CUSTOM:
            return
        if not data.get("custom_order"):
            raise ValidationError(
                "Custom strategy requires a priority order.",
                field_name="custom_order",
            )
