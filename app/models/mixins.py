"""
Shekel Budget App -- Shared Model Mixins

Centralizes column declarations that would otherwise repeat verbatim
across many model files.  SQLAlchemy mixins produce DDL identical to
inline column declarations; the only effect is to keep the canonical
definition in one place so a future change (e.g. timezone choice,
default precision) is a single edit instead of N edits.

Mixins are NOT registered in ``app/models/__init__.py`` -- they
represent shared declarations, not concrete tables.
"""

from app.extensions import db


class TimestampMixin:
    """Audit-trail timestamps for mutable rows.

    Adds two columns:

      ``created_at`` -- TIMESTAMPTZ NOT NULL DEFAULT NOW().  Set once
                        at INSERT time by the database default.
      ``updated_at`` -- TIMESTAMPTZ NOT NULL DEFAULT NOW(), refreshed
                        to NOW() on every UPDATE via SQLAlchemy's
                        ``onupdate`` hook.

    Use on tables where rows are edited after creation (most user
    data: accounts, transactions, settings, etc.).  For append-only
    history/event tables where ``updated_at`` would be misleading,
    use :class:`CreatedAtMixin` instead.
    """

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )


class CreatedAtMixin:
    """Single-timestamp variant for append-only history rows.

    Adds one column:

      ``created_at`` -- TIMESTAMPTZ NOT NULL DEFAULT NOW().

    Use on tables that record events at a moment in time and never
    update afterwards: anchor history, rate history, pay periods,
    salary raises, tax-year configurations, etc.  A separate
    ``updated_at`` would be misleading on these rows because they
    are not edited after the initial INSERT -- amendment is modeled
    as a new row, not an update of an existing row.
    """

    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now(),
    )
