"""
Shekel Budget App -- YTD Tax Checkpoint Model (salary schema)

A year-to-date checkpoint captured from a real pay stub: the five
cumulative figures (gross plus federal / state / Social Security /
Medicare withholding) measured through a given stub date.  The analytics
Taxes tab (T-P2..T-P4) anchors its withholding-to-date estimate on the
latest checkpoint in the tax year and models only the remaining periods,
turning the biggest error source (fully-modeled withholding) into
measured data.

History-keeping, NOT one-row-per-profile: a profile accumulates one
checkpoint per stub date so the deferred estimate-convergence chart can
re-plot the refund estimate at each saved checkpoint.  Re-entering a stub
for a date that already has a checkpoint REPLACES that row's figures (the
upsert path in ``tax_withholding_service.save_checkpoint``); a new date
inserts a new row.  The ``(salary_profile_id, as_of_date)`` unique
constraint is what makes that upsert well-defined.
"""

from app.extensions import db
from app.models.mixins import (
    SalaryProfileScopedMixin,
    TimestampMixin,
)


class YtdTaxCheckpoint(
    SalaryProfileScopedMixin, TimestampMixin, db.Model,
):
    """Year-to-date gross + withholding figures read off a real pay stub.

    Five cumulative money figures measured through ``as_of_date``: the
    YTD gross and the four YTD withholding lines (federal, state, Social
    Security, Medicare).  Each is a non-negative ``Numeric(12, 2)`` and
    each withholding line additionally CHECKs ``<= ytd_gross`` -- a
    withholding figure larger than gross is a data-entry typo, not a
    legitimate stub, and must be rejected at the storage tier as well as
    the schema tier.

    Multiple rows per profile (one per stub date, uniqued on
    ``(salary_profile_id, as_of_date)``): the update-from-stub form
    upserts on that pair, and the retained history feeds the deferred
    estimate-convergence chart.
    """

    __tablename__ = "ytd_tax_checkpoints"
    __table_args__ = (
        db.UniqueConstraint(
            "salary_profile_id", "as_of_date",
            name="uq_ytd_tax_checkpoints_profile_date",
        ),
        # Non-negativity: no YTD figure can be negative.  ``ytd_gross``
        # stays ``>= 0`` (not ``> 0``) on purpose -- an early-January stub
        # can be small but never negative, and a degenerate zero-gross
        # stub is a nonsense the >= bound still admits so the plausibility
        # checks below (component <= gross) do the rejecting.
        db.CheckConstraint(
            "ytd_gross >= 0",
            name="ck_ytd_tax_checkpoints_nonneg_gross",
        ),
        db.CheckConstraint(
            "ytd_federal >= 0",
            name="ck_ytd_tax_checkpoints_nonneg_federal",
        ),
        db.CheckConstraint(
            "ytd_state >= 0",
            name="ck_ytd_tax_checkpoints_nonneg_state",
        ),
        db.CheckConstraint(
            "ytd_social_security >= 0",
            name="ck_ytd_tax_checkpoints_nonneg_ss",
        ),
        db.CheckConstraint(
            "ytd_medicare >= 0",
            name="ck_ytd_tax_checkpoints_nonneg_medicare",
        ),
        # Plausibility: a single withholding line cannot exceed YTD gross.
        # A federal/state/FICA figure larger than the gross it was
        # withheld from is a typo (a swapped or extra digit); reject it at
        # the DB tier so a raw-SQL bypass of the schema layer cannot
        # persist an impossible stub that would corrupt the refund
        # estimate.
        db.CheckConstraint(
            "ytd_federal <= ytd_gross",
            name="ck_ytd_tax_checkpoints_federal_le_gross",
        ),
        db.CheckConstraint(
            "ytd_state <= ytd_gross",
            name="ck_ytd_tax_checkpoints_state_le_gross",
        ),
        db.CheckConstraint(
            "ytd_social_security <= ytd_gross",
            name="ck_ytd_tax_checkpoints_ss_le_gross",
        ),
        db.CheckConstraint(
            "ytd_medicare <= ytd_gross",
            name="ck_ytd_tax_checkpoints_medicare_le_gross",
        ),
        {"schema": "salary"},
    )

    id = db.Column(db.Integer, primary_key=True)

    # The pay-stub date the YTD figures are measured through.  The
    # producer treats a period as "already measured" when its payday
    # (start_date) is on or before this date.
    as_of_date = db.Column(db.Date, nullable=False)

    # Cumulative year-to-date figures read off the stub.
    ytd_gross = db.Column(db.Numeric(12, 2), nullable=False)
    ytd_federal = db.Column(db.Numeric(12, 2), nullable=False)
    ytd_state = db.Column(db.Numeric(12, 2), nullable=False)
    ytd_social_security = db.Column(db.Numeric(12, 2), nullable=False)
    ytd_medicare = db.Column(db.Numeric(12, 2), nullable=False)

    # Optional free-text note (e.g. which employer stub, mid-year job
    # change).  Nullable: a checkpoint needs no annotation to be valid.
    notes = db.Column(db.Text)

    # salary_profile_id: from SalaryProfileScopedMixin.
    # created_at / updated_at: from TimestampMixin (a re-entered stub
    # updates the existing row, so updated_at is meaningful here).

    salary_profile = db.relationship("SalaryProfile")

    def __repr__(self):
        return (
            f"<YtdTaxCheckpoint profile_id={self.salary_profile_id} "
            f"as_of={self.as_of_date} gross={self.ytd_gross}>"
        )
