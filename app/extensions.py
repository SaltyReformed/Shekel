"""
Shekel Budget App — Flask Extension Instances

Extensions are instantiated here (without an app) and bound to the
Flask app inside the create_app() factory.  This avoids circular
imports: models can import `db` from here without importing the app.
"""

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

# Database ORM
db = SQLAlchemy()

# Alembic migration manager
migrate = Migrate()

# Session-based authentication
login_manager = LoginManager()
login_manager.login_view = "auth.login"  # Redirect target for @login_required
login_manager.login_message_category = "warning"
