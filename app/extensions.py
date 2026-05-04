"""
Shekel Budget App -- Flask Extension Instances

Extensions are instantiated here (without an app) and bound to the
Flask app inside the create_app() factory.  This avoids circular
imports: models can import `db` from here without importing the app.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

# Database ORM
db = SQLAlchemy()

# Alembic migration manager
migrate = Migrate()

# Session-based authentication
login_manager = LoginManager()
login_manager.login_view = "auth.login"  # Redirect target for @login_required
login_manager.login_message_category = "warning"

# Session-fixation defence.  Flask-Login's default ("basic") flips the
# session's ``_fresh`` flag to False when the per-request session
# identifier (sha512 of remote address + User-Agent) drifts from the
# value captured at login_user() time, but leaves the rest of the
# session intact -- so an attacker who replays a stolen signed-cookie
# session from a different IP/UA would still be treated as the
# original user for any endpoint that does not require ``fresh_login``.
# "strong" mode pops every Flask-Login session key on identifier drift
# (see flask_login/login_manager.py:_session_protection_failed) and
# additionally schedules the remember-me cookie for clearing, forcing
# a complete re-authentication.  This is required for ASVS L2 V3.2.1
# (Session Protection) and closes audit finding F-038.  The protection
# is small defence-in-depth gain today (Flask's signed-cookie session
# already resists classic fixation) but is load-bearing once the
# project migrates to a server-side session store (planned remediation
# Commit C-53) where stolen session IDs would otherwise be replayable
# from any origin.  See docs/audits/security-2026-04-15/
# remediation-plan.md "Commit C-07".
login_manager.session_protection = "strong"

# CSRF protection
csrf = CSRFProtect()

# Rate limiting.  All operational settings (storage backend, default
# limits, swallow_errors, in-memory fallback, strategy) are intentionally
# resolved from app.config inside Limiter.init_app(), NOT from constructor
# arguments here.  Reason: Flask-Limiter 4.x (_extension.py:371-376)
# preferentially uses the constructor's storage_uri over the value in
# app.config[ConfigVars.STORAGE_URI], which means a value set here would
# silently override TestConfig's "memory://" override and ProdConfig's
# Redis URI.  Keeping the constructor minimal lets each environment
# (DevConfig / TestConfig / ProdConfig) own its own posture without
# touching this file.  See audit finding F-034 and remediation Commit
# C-06.
limiter = Limiter(key_func=get_remote_address)
