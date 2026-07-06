"""
Shekel Budget App -- Escrow line + version models (budget schema)

The normalized, supersession-based escrow model that will replace the
name-keyed ``budget.escrow_components`` table.  An escrow LINE
(:class:`EscrowLine`) is the stable identity and current display label of one
impound charge (property tax, insurance, PMI); its amount over time is a series
of effective-dated VERSIONS (:class:`EscrowComponentVersion`).

**Supersession, not ranges.**  A version is active from its ``effective_date``
until the NEXT version of the same line supersedes it -- there is no
``end_date``.  "Escrow as of date D for a line" is the version with the greatest
``effective_date <= D``; if that version ``is_removed`` (or the line has no
version on/before D), the line contributes nothing.  Because activeness is
defined by the ordering of a line's own versions, two versions of one line can
never overlap -- the overlapping-range state that produced the account-3
double-count is unrepresentable, with no exclusion constraint needed.

**Removal is a tombstone version.**  Dropping a line (PMI falls off) appends a
version with ``is_removed = True`` at the removal date; the line then resolves to
0 from that date until a later real version revives it.  ``is_removed`` is a
per-version IMMUTABLE event (removal happened, effective this date), NOT the
former mutable ``is_active`` boolean that could drift from state -- the same
reason a loan records a dated :class:`~app.models.loan_anchor_event.LoanAnchorEvent`
rather than mutating a balance column.

See ``docs/design/escrow_line_identity_refactor.md`` for the full design.  This
module is the Commit-1 EXPAND phase: the tables are created and backfilled
alongside the legacy :class:`~app.models.loan_features.EscrowComponent` (still
the live source until the Commit-2 reader cutover), so nothing reads these
models yet and behaviour is unchanged.
"""

from app.extensions import db
from app.models.mixins import AccountScopedMixin, TimestampMixin


class EscrowLine(AccountScopedMixin, TimestampMixin, db.Model):
    """One logical escrow line: its stable identity and current display label.

    The parent entity of the effective-dated escrow model.  Its ``id`` is the
    stable line identity that ties a line's versions together across a rename or
    an amount change (versions FK to it), replacing the mutable ``name`` string
    the legacy :class:`~app.models.loan_features.EscrowComponent` used as its
    identity key.  ``name`` here is display-only: a rename edits it in place and
    cannot move a cent of any posted loan-payment split (the split reads a
    version's amount + date, never the name).

    "At most one ACTIVE line per name per account" is enforced in the escrow
    route (a line is active iff its latest version is not ``is_removed`` -- a
    predicate that depends on the child versions, so it cannot be a raw partial
    unique index here; decision C in the design doc).  The
    ``ix_escrow_lines_account_name`` non-unique index serves that lookup and the
    per-account line load.
    """

    __tablename__ = "escrow_lines"
    __table_args__ = (
        db.Index("ix_escrow_lines_account_name", "account_id", "name"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    # Relationships
    versions = db.relationship(
        "EscrowComponentVersion",
        back_populates="line",
        cascade="all, delete-orphan",
        lazy="select",
    )
    account = db.relationship(
        "Account",
        backref=db.backref("escrow_lines", lazy="select"),
    )

    def __repr__(self):
        return (
            f"<EscrowLine id={self.id} account_id={self.account_id} "
            f"name={self.name!r}>"
        )


class EscrowComponentVersion(TimestampMixin, db.Model):
    """One effective-dated version of an escrow line's amount, or a removal tombstone.

    A child of :class:`EscrowLine`.  ``[effective_date, ...)`` is open-ended: the
    version is in effect from ``effective_date`` until the next version of the
    same ``line_id`` (a greater ``effective_date``) supersedes it, so there is no
    ``end_date`` and no way to represent an overlap.  ``annual_amount / 12`` is
    the monthly escrow the version contributes; ``is_removed = True`` marks a
    tombstone (the line contributes 0 from this date, ``annual_amount`` ignored
    and stored as 0).

    ``uq_escrow_component_versions_line_effective_date`` (``line_id``,
    ``effective_date``) permits at most one version per line per date -- a
    same-day correction edits that row rather than appending a second -- and
    doubles as the index for the
    as-of lookup (``WHERE line_id = ? AND effective_date <= ? ORDER BY
    effective_date DESC LIMIT 1``).  ``inflation_rate`` is a forward-projection
    display concern only (recorded past/present escrow is exact); the loan-payment
    split never applies it.
    """

    __tablename__ = "escrow_component_versions"
    __table_args__ = (
        # At most one version per line per effective date; also the as-of index.
        db.UniqueConstraint(
            "line_id", "effective_date",
            name="uq_escrow_component_versions_line_effective_date",
        ),
        # Annual escrow amount is non-negative (a tombstone stores 0).  Mirrors
        # ``EscrowComponent``'s storage-tier guard for raw-SQL writers.
        db.CheckConstraint(
            "annual_amount >= 0",
            name="ck_escrow_component_versions_nonneg_annual_amount",
        ),
        # ``inflation_rate`` is nullable (NULL = no escalation) and persisted as
        # a decimal fraction; the CHECK pins it to ``[0, 1]`` when present, as
        # ``escrow_components`` / ``paycheck_deductions`` do.
        db.CheckConstraint(
            "inflation_rate IS NULL OR "
            "(inflation_rate >= 0 AND inflation_rate <= 1)",
            name="ck_escrow_component_versions_valid_inflation_rate",
        ),
        # A removal tombstone contributes nothing, so its ``annual_amount`` is
        # meaningless and must be 0 -- the storage-tier counterpart to the
        # resolver keying on ``is_removed``.  Prevents a future writer from
        # creating a confusing tombstone that also carries a non-zero amount.
        db.CheckConstraint(
            "NOT is_removed OR annual_amount = 0",
            name="ck_escrow_component_versions_tombstone_zero_amount",
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    line_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "budget.escrow_lines.id", ondelete="CASCADE",
            name="fk_escrow_component_versions_line_id",
        ),
        nullable=False,
    )
    effective_date = db.Column(db.Date, nullable=False)
    annual_amount = db.Column(db.Numeric(12, 2), nullable=False)
    inflation_rate = db.Column(db.Numeric(5, 4), nullable=True)
    # A tombstone version: True means the line is removed as of ``effective_date``
    # (contributes 0 until a later real version revives it).  An immutable
    # per-version event, not a mutable line-state flag.
    is_removed = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )

    # Relationships
    line = db.relationship("EscrowLine", back_populates="versions")

    def __repr__(self):
        return (
            f"<EscrowComponentVersion id={self.id} line_id={self.line_id} "
            f"effective={self.effective_date} annual={self.annual_amount} "
            f"removed={self.is_removed}>"
        )
