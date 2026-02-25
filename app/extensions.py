"""
Shekel Budget App — Flask Extension Instances

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

# Rate limiting (in-memory, single-user app)
limiter = Limiter(key_func=get_remote_address, default_limits=[], storage_uri="memory://")
