"""Category create / edit validation schemas."""


from marshmallow import (
    fields,
    validate,
)

from app.schemas.validation._helpers import BaseSchema


class CategoryCreateSchema(BaseSchema):
    """Validates POST data for creating a category."""

    group_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    item_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    sort_order = fields.Integer(load_default=0)


class CategoryEditSchema(BaseSchema):
    """Validates POST data for editing a category (rename / re-parent)."""

    group_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    item_name = fields.String(required=True, validate=validate.Length(min=1, max=100))
