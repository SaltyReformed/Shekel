"""
Shekel Budget App -- User and Authentication Models (auth schema)

Includes the User model (Flask-Login compatible), user settings,
and MFA/TOTP configuration.
"""

from flask_login import UserMixin

from app.extensions import db
from app.models.mixins import TimestampMixin


class User(UserMixin, TimestampMixin, db.Model):
    """Application user.  Flask-Login's UserMixin provides is_authenticated, etc.

    Account-lockout columns (see audit finding F-033 / commit C-11):

      * ``failed_login_count`` -- count of consecutive bad-password
        attempts.  Incremented inside ``auth_service.authenticate`` on
        every failed verify, reset to ``0`` on a successful login or
        when the threshold trips and ``locked_until`` is stamped.
      * ``locked_until`` -- exclusive upper bound on the lockout window;
        ``NULL`` outside lockout.  ``authenticate`` short-circuits and
        raises ``AuthError`` without running ``verify_password`` while
        ``locked_until > now`` so a brute-force attacker observes
        constant-time rejections during the lockout window (no timing
        oracle on whether the password was correct on the locked
        account).

    Together these columns implement per-account brute-force throttling
    that does not depend on Flask-Limiter's IP-keyed storage.  IP
    rotation (residential proxy, RFC 1918 spoofing) cannot bypass the
    per-account counter -- only knowing the password before the
    lockout trips can.  Threshold and duration are env-configurable
    via ``LOCKOUT_THRESHOLD`` and ``LOCKOUT_DURATION_MINUTES``; see
    ``BaseConfig``.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Defensive lower bound on the lockout counter.  The service
        # only ever increments from a non-negative starting value, but
        # a future raw-SQL backfill or a buggy migration that wrote a
        # negative value would otherwise quietly invert the lockout
        # logic (the "<= 0 means no lockout yet" branch would never
        # trip).  The CHECK is the database-side belt to the
        # service-side suspenders.
        db.CheckConstraint(
            "failed_login_count >= 0",
            name="ck_users_failed_login_count_non_negative",
        ),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    # Timestamp of most recent "log out all sessions" or password change event.
    # The user loader compares this against the session creation time to reject stale sessions.
    session_invalidated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Number of consecutive failed login attempts since the last
    # successful authenticate() or lockout-trip.  NOT NULL with a
    # server default of 0 so existing rows backfill cleanly and any
    # raw-SQL INSERT that omits the column still produces a usable
    # row.  See audit finding F-033 / commit C-11.
    failed_login_count = db.Column(
        db.Integer, nullable=False, server_default="0",
    )
    # Exclusive upper bound on the active lockout window, or NULL when
    # the account is not locked.  Comparison is strict greater-than:
    # at the instant the column equals ``now`` the lockout is over.
    # Nullable because most rows are NOT in lockout at any given moment
    # -- the column carries the "no active lockout" state as NULL
    # rather than as a sentinel datetime in the past, which keeps the
    # service-side check (``locked_until is not None and locked_until
    # > now``) explicit about both conditions.  See audit finding
    # F-033 / commit C-11.
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    role_id = db.Column(
        db.Integer,
        db.ForeignKey("ref.user_roles.id", ondelete="RESTRICT"),
        nullable=False,
        server_default="1",  # 1 = owner
    )
    linked_owner_id = db.Column(
        db.Integer,
        db.ForeignKey("auth.users.id", ondelete="SET NULL"),
    )

    # Relationships
    settings = db.relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    role = db.relationship("UserRole", lazy="joined")

    def __repr__(self):
        return f"<User {self.email}>"


class UserSettings(TimestampMixin, db.Model):
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

    # Back-reference to User
    user = db.relationship("User", back_populates="settings")
    default_grid_account = db.relationship(
        "Account", lazy="joined",
        foreign_keys=[default_grid_account_id],
    )

    def __repr__(self):
        return f"<UserSettings user_id={self.user_id}>"


class MfaConfig(TimestampMixin, db.Model):
    """MFA/TOTP configuration for a user.

    Stores the encrypted TOTP secret, enabled state, hashed backup
    codes, and confirmation timestamp.  One-to-one with auth.users
    (user_id is unique).

    The TOTP secret is encrypted at rest using Fernet symmetric
    encryption (key from TOTP_ENCRYPTION_KEY env var).  Backup codes
    are stored as a JSON list of bcrypt hashes.

    During an in-progress /mfa/setup flow the unconfirmed secret is
    held in ``pending_secret_encrypted`` (server-side, encrypted under
    the same MultiFernet as ``totp_secret_encrypted``) rather than in
    the user's session cookie, which Flask only signs and does not
    encrypt.  ``pending_secret_expires_at`` bounds the window in which
    the pending secret can be promoted to ``totp_secret_encrypted`` --
    abandoned setups become unusable on their own.  See audit finding
    F-031 / commit C-05.

    Replay prevention: ``last_totp_timestep`` records the highest 30-
    second time-step ever accepted from this row.  Subsequent verifies
    must produce a strictly greater step or are rejected as replays.
    See audit findings F-005, F-142 / commit C-09.

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
    # Pending TOTP secret captured during /mfa/setup but not yet
    # confirmed by a valid TOTP code on /mfa/confirm.  Encrypted under
    # the same Fernet/MultiFernet key as ``totp_secret_encrypted``.
    # Nullable because most rows are NOT mid-setup at any given moment
    # (no setup ever started, or the most recent setup was confirmed,
    # expired, or abandoned and cleared).
    pending_secret_encrypted = db.Column(db.LargeBinary, nullable=True)
    # Wall-clock expiry of ``pending_secret_encrypted``.  /mfa/confirm
    # rejects pending state once this timestamp has passed so an
    # abandoned setup cannot be completed weeks later by an attacker
    # who briefly gains access to the account.  Nullable for the same
    # reason as ``pending_secret_encrypted``: most rows have no pending
    # setup in progress.
    pending_secret_expires_at = db.Column(
        db.DateTime(timezone=True), nullable=True,
    )
    is_enabled = db.Column(db.Boolean, default=False)
    backup_codes = db.Column(db.JSON)
    confirmed_at = db.Column(db.DateTime(timezone=True))
    # Highest TOTP time-step (Unix-seconds // 30) that the user has
    # successfully presented.  Replay prevention rejects any code that
    # decodes to a step less than or equal to this value -- without it,
    # a 30-second TOTP code with the standard +-1 drift window remains
    # replayable for ~90 seconds after observation.  See ASVS V2.8.4
    # and audit findings F-005, F-142 / commit C-09 of the 2026-04-15
    # security remediation plan.
    #
    # Nullable for two complementary reasons: rows that pre-date this
    # column have no recorded step (and the first successful verify
    # populates it), and rows where MFA has been disabled clear it
    # back to NULL so a re-enrollment under a new secret does not
    # inherit a stale step boundary.
    last_totp_timestep = db.Column(db.BigInteger, nullable=True)
