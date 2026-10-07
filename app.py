from flask import Flask

from db import init_db
from routes import register_blueprints


def create_app() -> Flask:
    app = Flask(__name__)

    init_db()
    register_blueprints(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=3000, debug=True)