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
