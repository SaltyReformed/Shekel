import os
from flask import Flask, render_template
from config import Config
from models import db
from auth import auth_bp
from income import income_bp
from config_manager import config_bp  # Import from config_manager instead of config


def create_app():
    # Get the absolute path to the app directory
    base_dir = os.path.abspath(os.path.dirname(__file__))
    template_dir = os.path.join(base_dir, "app", "templates")
    static_dir = os.path.join(base_dir, "app", "static")

    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    app.config.from_object(Config)
    db.init_app(app)

    with app.app_context():
        db.create_all()

    # Register the blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(income_bp)
    app.register_blueprint(config_bp)  # Register the config blueprint

    @app.route("/")
    def home():
        return render_template("index.html")

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
