from flask import Flask, render_template
from config import Config
from models import db
from auth import auth_bp  # Ensure auth.py is in the same directory


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    with app.app_context():
        db.create_all()

    # Register the blueprint without a prefix
    app.register_blueprint(auth_bp)

    @app.route("/")
    def home():
        return render_template("index.html")

    # (Optional) Print URL map for debugging
    print(app.url_map)

    return app
