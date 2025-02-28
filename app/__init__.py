from flask import Flask, render_template
from config import Config
from models import db


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize the database with the app
    db.init_app(app)

    # Simple route to render the index page
    @app.route("/")
    def index():
        return render_template("index.html")

    return app
