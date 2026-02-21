"""
Shekel Budget App — Scenario Model (budget schema)

A scenario is a named version of the budget.  Phase 1 uses only the
baseline scenario; Phase 3 adds clone, compare, and diff features.
"""

from app.extensions import db


class Scenario(db.Model):
    """A named budget scenario (baseline or what-if variant)."""

    __tablename__ = "scenarios"
    __table_args__ = (
        db.UniqueConstraint("user_id", "name", name="uq_scenarios_user_name"),
        {"schema": "budget"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = db.Column(db.String(100), nullable=False)
    is_baseline = db.Column(db.Boolean, default=False)
    cloned_from_id = db.Column(
        db.Integer, db.ForeignKey("budget.scenarios.id", ondelete="SET NULL")
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    def __repr__(self):
        return f"<Scenario '{self.name}' baseline={self.is_baseline}>"
