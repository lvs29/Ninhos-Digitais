# routes/especies.py
from flask import Blueprint, render_template, abort

import db

especies_bp = Blueprint("especies", __name__)


@especies_bp.route("/e/<codigo>")
def pagina_publica(codigo):
    especie = db.obter_especie_por_codigo(codigo)
    if not especie:
        abort(404)
    return render_template("especie.html", especie=especie)