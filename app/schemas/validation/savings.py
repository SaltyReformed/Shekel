"""Savings-goal create / update validation schemas."""


from decimal import Decimal

from marshmallow import (
    fields,
    pre_load,
    validate,
    validates_schema,
    ValidationError,
)

from app import ref_cache
from app.enums import (
    GoalModeEnum,
    IncomeUnitEnum,
)
from app.schemas.validation._helpers import BaseSchema


class SavingsGoalCreateSchema(BaseSchema):
    """Validates POST data for creating a savings goal.

    Supports two goal modes:

        Fixed (default): target_amount is required; income fields
        must be absent.

        Income-Relative: income_unit_id and income_multiplier are
        required; target_amount is optional (calculated on read).

    Cross-field rules are enforced in validate_goal_mode_fields().
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Remove empty-string form values so Marshmallow sees missing fields."""
        return {k: v for k, v in data.items() if v != ""}

    account_id = fields.Integer(required=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    target_amount = fields.Decimal(
        load_default=None, allow_none=True,
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    target_date = fields.Date()
    # F-106 / C-25: DB CHECK enforces ``contribution_per_period IS NULL
    # OR contribution_per_period > 0``.  Schema must reject 0 too (the
    # previous ``min=0`` inclusive bound would defer the rejection to
    # the database, surfacing as a 500 IntegrityError instead of a
    # field-level 400).  ``allow_none=True`` matches the column's
    # nullability so JSON callers can clear the contribution
    # explicitly; the form path is already covered by
    # ``strip_empty_strings`` above.
    contribution_per_period = fields.Decimal(
        load_default=None, allow_none=True,
        places=2, as_string=True,
        validate=validate.Range(min=Decimal("0"), min_inclusive=False),
    )
    goal_mode_id = fields.Integer(load_default=1)
    income_unit_id = fields.Integer(load_default=None, allow_none=True)
    income_multiplier = fields.Decimal(
        load_default=None, allow_none=True,
        places=2, as_string=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )

    @validates_schema
    def validate_goal_mode_fields(self, data, **kwargs):
        """Enforce cross-field constraints between goal mode and income fields."""

        goal_mode_id = data.get("goal_mode_id", 1)
        income_unit_id = data.get("income_unit_id")
        income_multiplier = data.get("income_multiplier")
        target_amount = data.get("target_amount")

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        income_relative_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

        # Validate goal_mode_id is a known mode.
        if goal_mode_id not in (fixed_id, income_relative_id):
            raise ValidationError(
                "Invalid goal mode.", field_name="goal_mode_id",
            )

        if goal_mode_id == fixed_id:
            # Fixed mode: income fields must be absent.
            if income_unit_id is not None:
                raise ValidationError(
                    "Income unit must be empty for fixed-amount goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is not None:
                raise ValidationError(
                    "Income multiplier must be empty for fixed-amount goals.",
                    field_name="income_multiplier",
                )
            if target_amount is None:
                raise ValidationError(
                    "Target amount is required for fixed-amount goals.",
                    field_name="target_amount",
                )

        elif goal_mode_id == income_relative_id:
            # Income-relative mode: income fields are required.
            if income_unit_id is None:
                raise ValidationError(
                    "Income unit is required for income-relative goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is None:
                raise ValidationError(
                    "Income multiplier is required for income-relative goals.",
                    field_name="income_multiplier",
                )
            # Validate income_unit_id is a known unit.
            known_units = (
                ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS),
                ref_cache.income_unit_id(IncomeUnitEnum.MONTHS),
            )
            if income_unit_id not in known_units:
                raise ValidationError(
                    "Invalid income unit.", field_name="income_unit_id",
                )


class SavingsGoalUpdateSchema(BaseSchema):
    """Validates PUT data for updating a savings goal.

    Same cross-field rules as SavingsGoalCreateSchema.  The goal_mode_id
    defaults to None (not provided) for updates -- the cross-field
    validator only fires when goal_mode_id is explicitly included in
    the update payload.

    ``version_id`` is the optimistic-locking counter; see
    :class:`TransactionUpdateSchema` for the contract.
    """

    @pre_load
    def strip_empty_strings(self, data, **kwargs):
        """Remove empty-string form values so Marshmallow sees missing fields."""
        return {k: v for k, v in data.items() if v != ""}

    account_id = fields.Integer()
    name = fields.String(validate=validate.Length(min=1, max=100))
    target_amount = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )
    target_date = fields.Date(allow_none=True)
    # F-106 / C-25: see :class:`SavingsGoalCreateSchema` for the
    # boundary-inclusivity rationale.  Update path also accepts
    # ``None`` to clear the contribution.
    contribution_per_period = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=Decimal("0"), min_inclusive=False),
    )
    is_active = fields.Boolean()
    goal_mode_id = fields.Integer()
    income_unit_id = fields.Integer(allow_none=True)
    income_multiplier = fields.Decimal(
        places=2, as_string=True, allow_none=True,
        validate=validate.Range(min=0, min_inclusive=False),
    )

    # Optimistic-locking pin (commit C-18).
    version_id = fields.Integer(validate=validate.Range(min=1))

    @validates_schema
    def validate_goal_mode_fields(self, data, **kwargs):
        """Enforce cross-field constraints between goal mode and income fields.

        Only validates when goal_mode_id is present in the update payload.
        Partial updates that omit goal_mode_id skip cross-field checks.
        """

        goal_mode_id = data.get("goal_mode_id")
        if goal_mode_id is None:
            return

        income_unit_id = data.get("income_unit_id")
        income_multiplier = data.get("income_multiplier")
        target_amount = data.get("target_amount")

        fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        income_relative_id = ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)

        if goal_mode_id not in (fixed_id, income_relative_id):
            raise ValidationError(
                "Invalid goal mode.", field_name="goal_mode_id",
            )

        if goal_mode_id == fixed_id:
            if income_unit_id is not None:
                raise ValidationError(
                    "Income unit must be empty for fixed-amount goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is not None:
                raise ValidationError(
                    "Income multiplier must be empty for fixed-amount goals.",
                    field_name="income_multiplier",
                )
            if target_amount is None:
                raise ValidationError(
                    "Target amount is required for fixed-amount goals.",
                    field_name="target_amount",
                )

        elif goal_mode_id == income_relative_id:
            if income_unit_id is None:
                raise ValidationError(
                    "Income unit is required for income-relative goals.",
                    field_name="income_unit_id",
                )
            if income_multiplier is None:
                raise ValidationError(
                    "Income multiplier is required for income-relative goals.",
                    field_name="income_multiplier",
                )
            known_units = (
                ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS),
                ref_cache.income_unit_id(IncomeUnitEnum.MONTHS),
            )
            if income_unit_id not in known_units:
                raise ValidationError(
                    "Invalid income unit.", field_name="income_unit_id",
                )
