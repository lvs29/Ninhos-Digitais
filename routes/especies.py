# routes/especies.py
from flask import Blueprint, abort, current_app, g, render_template

import db

especies_bp = Blueprint("especies", __name__)


def _registrar_acesso(tag_id: int) -> None:
    """Conta o acesso. Visitas de administradores logados (pré-visualização) não contam,
    e uma falha ao gravar a contagem nunca deve derrubar a página pública."""
    if g.get("usuario"):
        return
    try:
        db.registrar_acesso(tag_id)
    except Exception:
        current_app.logger.exception("Falha ao registrar acesso da tag %s", tag_id)


@especies_bp.route("/e/<codigo>")
def pagina_publica(codigo):
    if len(codigo) > 64:  # nenhum código válido é maior que isso
        abort(404)
    tag = db.obter_tag_por_codigo(codigo)
    if not tag:
        abort(404)

    _registrar_acesso(tag["id"])

    # Só as espécies marcadas como visíveis aparecem para o público
    especies = db.listar_especies_por_codigo(codigo, apenas_visiveis=True)
    if not especies:
        return render_template("tag_sem_especie.html")

    return render_template("viveiro.html", especies=especies)