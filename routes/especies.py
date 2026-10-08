# routes/especies.py
from flask import Blueprint, render_template, abort

import db

especies_bp = Blueprint("especies", __name__)


@especies_bp.route("/e/<codigo>")
def pagina_publica(codigo):
    tag = db.obter_tag_por_codigo(codigo)
    if not tag:
        abort(404)

    especie = db.obter_especie_por_codigo(codigo)
    if not especie:
        # A tag existe, mas ainda não foi associada a nenhuma espécie
        return render_template("tag_sem_especie.html")

    return render_template("especie.html", especie=especie)