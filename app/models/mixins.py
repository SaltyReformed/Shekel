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


class SoftDeleteOverridableMixin:
    """Override and soft-delete flags for canonical/shadow rows.

    Adds two columns -- both ``BOOLEAN NOT NULL DEFAULT FALSE``:

      ``is_override`` -- True when the row was manually edited and
                         must NOT be regenerated/overwritten by the
                         recurrence engine.
      ``is_deleted``  -- True when the row was soft-deleted by the
                         user; remains in the table so historical
                         queries and audit triggers see the full
                         lifecycle, but ``effective_amount`` and
                         balance-relevant queries treat it as
                         absent.

    Used by :class:`Transaction` and :class:`Transfer`.  The columns
    are declared at class level (NOT via ``@declared_attr``) so the
    SQLAlchemy DDL is byte-identical to the pre-mixin inline
    declarations; ``flask db migrate --autogenerate`` against a
    migrated schema must produce an empty diff.

    Do NOT apply this mixin to (Transaction|Transfer)Template -- the
    template tables have ``is_active`` instead, with semantics that
    differ from soft-delete (an inactive template stops generating
    new rows but its historical rows remain valid).  Adding
    ``is_override`` / ``is_deleted`` to the template tables would be
    a schema change, not a refactor, and is out of scope for the
    duplicate-code cleanup the audit's Issue 1 names.
    """

    is_override = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    is_deleted = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
