"""
Shekel Budget App -- User and Authentication Models (auth schema)

Includes the User model (Flask-Login compatible), user settings,
and MFA/TOTP configuration.
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
    # Timestamp of most recent "log out all sessions" or password change event.
    # The user loader compares this against the session creation time to reject stale sessions.
    session_invalidated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationships
    settings = db.relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User {self.email}>"


class UserSettings(db.Model):
    """Per-user application preferences (grid defaults, inflation rate, etc.)."""

    __tablename__ = "user_settings"
    __table_args__ = (
        db.CheckConstraint(
            "default_inflation_rate >= 0 AND default_inflation_rate <= 1",
            name="ck_user_settings_valid_inflation",
        ),
        db.CheckConstraint("grid_default_periods > 0", name="ck_user_settings_positive_periods"),
        db.CheckConstraint("low_balance_threshold >= 0", name="ck_user_settings_positive_threshold"),
        db.CheckConstraint(
            "large_transaction_threshold >= 0",
            name="ck_user_settings_large_txn_threshold",
        ),
        db.CheckConstraint(
            "trend_alert_threshold >= 0 AND trend_alert_threshold <= 1",
            name="ck_user_settings_valid_trend_threshold",
        ),
        db.CheckConstraint(
            "anchor_staleness_days > 0",
            name="ck_user_settings_positive_staleness_days",
        ),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    default_inflation_rate = db.Column(db.Numeric(5, 4), default=0.0300)
    grid_default_periods = db.Column(db.Integer, default=6)
    low_balance_threshold = db.Column(db.Integer, default=500)
    safe_withdrawal_rate = db.Column(db.Numeric(5, 4), default=0.0400)
    planned_retirement_date = db.Column(db.Date, nullable=True)
    estimated_retirement_tax_rate = db.Column(db.Numeric(5, 4), nullable=True)
    large_transaction_threshold = db.Column(
        db.Integer, nullable=False, server_default="500",
    )
    trend_alert_threshold = db.Column(
        db.Numeric(5, 4), nullable=False, server_default="0.1000",
    )
    anchor_staleness_days = db.Column(
        db.Integer, nullable=False, server_default="14",
    )
    default_grid_account_id = db.Column(
        db.Integer,
        db.ForeignKey("budget.accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    # Back-reference to User
    user = db.relationship("User", back_populates="settings")
    default_grid_account = db.relationship(
        "Account", lazy="joined",
        foreign_keys=[default_grid_account_id],
    )

    def __repr__(self):
        return f"<UserSettings user_id={self.user_id}>"


class MfaConfig(db.Model):
    """MFA/TOTP configuration for a user.

    Stores the encrypted TOTP secret, enabled state, hashed backup
    codes, and confirmation timestamp.  One-to-one with auth.users
    (user_id is unique).

    The TOTP secret is encrypted at rest using Fernet symmetric
    encryption (key from TOTP_ENCRYPTION_KEY env var).  Backup codes
    are stored as a JSON list of bcrypt hashes.

    Related service: app/services/mfa_service.py
    Related routes: /mfa/setup, /mfa/confirm, /mfa/verify, /mfa/disable
    """

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
