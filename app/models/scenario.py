"""
Shekel Budget App -- Scenario Model (budget schema)

A scenario is a named version of the budget.  Phase 1 uses only the
baseline scenario; Phase 3 adds clone, compare, and diff features.
"""

from app.extensions import db
from app.models.mixins import TimestampMixin


class Scenario(TimestampMixin, db.Model):
    """A named budget scenario (baseline or what-if variant)."""

    __tablename__ = "scenarios"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_scenarios_user_name"),
        # H-1 of model-migration-drift: at most one baseline scenario per
        # user.  Production carries this partial unique index from
        # migration c5d6e7f8a901_add_positive_amount_check_constraints.py;
        # the model-side declaration keeps db.create_all() (the test path)
        # in sync so the suite exercises the same constraint production
        # enforces.  Baseline scenarios are the load-bearing reference for
        # every balance projection -- two baselines for the same user is
        # a logic-corruption bug (the calculator picks an arbitrary one
        # and projections drift).
        db.Index(
            "uq_scenarios_one_baseline",
            "user_id",
            unique=True,
            postgresql_where=db.text("is_baseline = true"),
        ),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    is_baseline = db.Column(
        db.Boolean, nullable=False, default=False,
        server_default=db.text("false"),
    )
    cloned_from_id = db.Column(
        db.Integer, db.ForeignKey("budget.scenarios.id", ondelete="SET NULL")
    )

    def __repr__(self):
        return f"<Scenario '{self.name}' baseline={self.is_baseline}>"
