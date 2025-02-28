import os
from flask import Flask, render_template, redirect, url_for, session, flash
from flask_wtf.csrf import CSRFProtect
from config import Config
from models import db
from auth import auth_bp
from income import income_bp
from config_manager import config_bp
from user_management import user_bp  # Import the new user management blueprint


def create_app():
    # Get the absolute path to the app directory
    base_dir = os.path.abspath(os.path.dirname(__file__))
    template_dir = os.path.join(base_dir, "app", "templates")
    static_dir = os.path.join(base_dir, "app", "static")

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    app.config.from_object(Config)
    csrf = CSRFProtect(app)
    db.init_app(app)

    # Create all tables
    with app.app_context():
        db.create_all()

    # Register the blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(income_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(user_bp)  # Register the user management blueprint

    # Helper function to check if user is logged in
    def is_logged_in():
        return "user_id" in session

    # Home route - shows different page based on login status
    @app.route("/")
    def home():
        if is_logged_in():
            return redirect(url_for("dashboard"))
        return render_template("index.html")

    # Dashboard route for logged-in users
    @app.route("/dashboard")
    def dashboard():
        if not is_logged_in():
            flash("Please log in to access the dashboard.", "danger")
            return redirect(url_for("auth.login"))

        return render_template("dashboard.html")

    # Make user session info available to all templates
    @app.context_processor
    def inject_user_info():
        user_info = {
            "is_logged_in": is_logged_in(),
            "is_admin": session.get("role") == "ADMIN" if is_logged_in() else False,
        }
        return user_info

    # Debug route
    @app.route("/test")
    def test():
        return f"""
        <html>
        <body>
            <h1>Debug Information</h1>
            <p>Template Folder: {app.template_folder}</p>
            <p>Static Folder: {app.static_folder}</p>
            <h2>Available Templates:</h2>
            <ul>
                {"".join([f"<li>{file}</li>" for file in os.listdir(app.template_folder) 
                          if os.path.isfile(os.path.join(app.template_folder, file))])}
            </ul>
            <h2>Routes:</h2>
            <ul>
                {"".join([f"<li>{rule}</li>" for rule in app.url_map.iter_rules()])}
            </ul>
        </body>
        </html>
        """

    return app
