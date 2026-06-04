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

from sqlalchemy.orm import declared_attr

from app.extensions import db


class UserScopedMixin:
    """Owning-user foreign key for per-user data tables.

    Adds one column:

      ``user_id`` -- INTEGER NOT NULL, ``FK auth.users.id ON DELETE
                     CASCADE``.  Identifies the user who owns the row;
                     deleting a user cascades to every row they own.

    Applied to the user-owned tables whose ``user_id`` is exactly this
    shape: a NOT NULL CASCADE FK with no ``unique`` qualifier.  Three
    ``user_id`` columns are deliberately EXCLUDED because their DDL
    differs:

      * ``ref.*`` per-user override rows (``AccountType``) -- the FK is
        ``ON DELETE RESTRICT`` and ``nullable=True`` (a NULL ``user_id``
        marks a seeded, system-owned row).
      * ``auth.user_settings`` / ``auth.mfa_configs`` -- 1:1 satellite
        tables whose ``user_id`` carries ``unique=True``.

    ``Transaction`` has NO ``user_id`` at all -- it is scoped through
    ``pay_period_id`` / ``account_id`` -- so it does not use this mixin.

    DDL-ORDERING NOTE (differs from the end-positioned mixins below).
    SQLAlchemy renders mixin columns AFTER a class's own columns, so
    extracting this previously-second column moves ``user_id`` to the
    tail of the emitted ``CREATE TABLE``.  This is NOT byte-identical to
    the prior inline declaration -- unlike :class:`OptimisticLockMixin`
    et al., whose columns already sat at the table tail.  It is
    nonetheless safe here, and the correct verification standard for
    this mixin is **order-independent equivalence + an empty Alembic
    autogenerate diff**, not byte-identical DDL, because column ORDER is
    load-bearing nowhere in this project:

      * the test suite clones the Alembic-migrated ``shekel_test_template``
        (column order comes from the migration chain, not the model);
      * no code or test does positional row/column access on these
        tables (the ORM addresses every column by name);
      * the documented ``db.create_all()`` <-> migration alignment
        invariant is about CONSTRAINT NAMES and existence, never order.

    Alembic autogenerate compares columns by name, so the reorder
    produces no migration.  If the auth-FK policy ever changes (e.g. a
    different ``ondelete``, or an added FK index), it is now a single
    edit here instead of ~15.
    """

    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )


class SortOrderMixin:
    """User-controlled display-ordering column.

    Adds one column:

      ``sort_order`` -- INTEGER NOT NULL DEFAULT 0.  A user-assignable
                        rank used to order rows in dropdowns and lists;
                        lower sorts first, ties broken by name.

    Applied to every table whose ``sort_order`` is exactly this shape
    (accounts, categories, salary profiles, both template tables,
    paycheck deductions, tax brackets).  Like :class:`UserScopedMixin`
    this is a mid-table column, so extracting it reorders the column to
    the table tail; the same order-independence argument applies (see
    :class:`UserScopedMixin` -- order is load-bearing nowhere here, so
    the standard is order-independent equivalence + empty autogenerate
    diff, not byte-identical DDL).
    """

    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default=db.text("0"),
    )


class IsActiveMixin:
    """Soft-enable / archive flag.

    Adds one column:

      ``is_active`` -- BOOLEAN NOT NULL DEFAULT TRUE.  False archives the
                       row: it stops driving new work (an inactive
                       template generates nothing; an inactive account is
                       hidden) while its historical rows remain valid.

    Distinct from :class:`SoftDeleteOverridableMixin`'s ``is_deleted``:
    ``is_active`` is a forward-looking enable switch the user toggles,
    whereas ``is_deleted`` is a soft-delete tombstone.  Applied to every
    table whose ``is_active`` is exactly this shape.  Same mid-table
    reorder + order-independence argument as :class:`UserScopedMixin`.

    EXCLUDES :class:`~app.models.user.User`.  ``User`` inherits
    Flask-Login's ``UserMixin``, which defines ``is_active`` as a
    property returning ``True``.  ``User`` declares its ``is_active``
    Column inline so the class attribute overrides that property; routing
    it through this mixin instead would put ``UserMixin.is_active`` ahead
    of the mixin Column in the MRO and silently shadow the database
    column.  ``User.is_active`` therefore stays inline by design.
    """

    is_active = db.Column(
        db.Boolean, nullable=False, default=True,
        server_default=db.text("true"),
    )


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


class TrackingVisibilityMixin:
    """Purchase-tracking and companion-visibility flags.

    Adds two columns -- both ``BOOLEAN NOT NULL DEFAULT FALSE``:

      ``is_envelope``       -- enables individual purchase entries
                               (the "envelope" budgeting mode where a
                               row accumulates per-purchase line items).
      ``companion_visible`` -- exposes the row in the linked companion's
                               read-only view.

    Used by both :class:`TransactionTemplate` and :class:`Transaction`.
    The flags mean the same thing on each, but resolve differently: a
    template-generated transaction inherits the template's flags (the
    template is the source of truth for every instance it generates),
    while an ad-hoc transaction -- which has no template -- carries its
    own.  ``Transaction.tracks_purchases`` and
    ``Transaction.visible_to_companion`` encode that resolution.

    Unlike :class:`SoftDeleteOverridableMixin`, this mixin IS safe on
    the template table: the columns already exist there with identical
    semantics, so applying the mixin is a pure refactor (single
    canonical definition), not a schema change.  Columns are declared at
    class level (NOT via ``@declared_attr``) so the SQLAlchemy DDL is
    byte-identical to the prior inline declarations; an autogenerate
    diff against a migrated schema must be empty.
    """

    is_envelope = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )
    companion_visible = db.Column(
        db.Boolean, nullable=False, default=False, server_default="false",
    )


class OptimisticLockMixin:
    """Optimistic-locking version counter for concurrently-edited rows.

    Adds one column plus the mapper configuration that activates
    SQLAlchemy's version-counter concurrency control:

      ``version_id`` -- INTEGER NOT NULL DEFAULT 1.  SQLAlchemy issues
                        ``UPDATE ... WHERE id = ? AND version_id = ?`` on
                        every flush of a dirty row, atomically increments
                        the counter in the same statement, and raises
                        ``StaleDataError`` when the rowcount is 0 -- i.e.
                        a concurrent commit already advanced the counter.

    Routes that mutate a row carrying this mixin MUST catch
    ``StaleDataError`` and surface a 409 Conflict (or a flash + redirect
    for full-page forms) so the loser retries against fresh state.  This
    is the commit C-18 / F-010 optimistic-locking contract.

    ``__mapper_args__`` is supplied via ``@declared_attr`` -- not a plain
    class attribute -- because declarative copies the mixin's
    ``version_id`` Column onto each subclass, and the ``version_id_col``
    mapper option must point at THAT subclass's own copy.  A plain
    ``{"version_id_col": version_id}`` dict would capture the mixin's
    original (unmapped) column and misconfigure every subclass.  The
    column itself is declared at class level (NOT via ``@declared_attr``)
    so the emitted DDL is byte-identical to the prior inline
    declarations; ``flask db migrate`` against a migrated schema must
    produce an empty diff.

    A model that needs its own ``__mapper_args__`` keys (e.g. polymorphic
    config) cannot use this mixin as-is -- it would have to merge the
    ``version_id_col`` entry into its own declared-attr.  None of the
    current optimistic-locked tables do.
    """

    version_id = db.Column(
        db.Integer, nullable=False, server_default="1",
    )

    @declared_attr
    def __mapper_args__(cls):  # pylint: disable=no-self-argument
        # declared_attr passes the mapped class, not an instance; the
        # `cls` name is the SQLAlchemy-mandated convention here.
        return {"version_id_col": cls.version_id}
