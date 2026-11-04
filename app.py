import os
from flask import Flask
from dotenv import load_dotenv

from db import init_db
from routes import register_blueprints
from security import init_security

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ["SECRET_KEY"]
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # limite dos arquivos enviados (importação)

    init_db()
    init_security(app)
    register_blueprints(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=3000, debug=True)