from flask import Flask

from .main import main_bp
from .especies import especies_bp
from .admin import admin_bp
from .auth import auth_bp

def register_blueprints(app: Flask) -> None:
    """Registra todos os blueprints da aplicação.

    Para adicionar um novo grupo de rotas, crie o arquivo em routes/
    (ex.: routes/especies.py), defina o Blueprint e registre aqui.
    """
    app.register_blueprint(main_bp)
    app.register_blueprint(especies_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp)
