"""
Shekel Budget App — User & Authentication Models (auth schema)

Includes the User model (Flask-Login compatible), user settings,
and an MFA stub table for Phase 6+.
"""

from flask_login import UserMixin

from app.extensions import db


class User(UserMixin, db.Model):
    """Application user.  Flask-Login's UserMixin provides is_authenticated, etc."""

    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Relationships
    settings = db.relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User {self.email}>"


class UserSettings(db.Model):
    """Per-user application preferences (grid defaults, inflation rate, etc.)."""

    __tablename__ = "user_settings"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    default_inflation_rate = db.Column(db.Numeric(5, 4), default=0.0300)
    grid_default_periods = db.Column(db.Integer, default=6)
    low_balance_threshold = db.Column(db.Integer, default=500)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Back-reference to User
    user = db.relationship("User", back_populates="settings")

    def __repr__(self):
        return f"<UserSettings user_id={self.user_id}>"


class MfaConfig(db.Model):
    """Stub table for Phase 6+ MFA/TOTP feature.  Schema only — no logic yet."""

    __tablename__ = "mfa_configs"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    totp_secret_encrypted = db.Column(db.LargeBinary)
    is_enabled = db.Column(db.Boolean, default=False)
    backup_codes = db.Column(db.JSON)
    confirmed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )
