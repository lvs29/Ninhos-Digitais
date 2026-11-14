from flask import Blueprint, abort, g, send_from_directory

import db
import imagens as armazem

midia_bp = Blueprint("midia", __name__, url_prefix="/midia")


@midia_bp.route("/<token>/<tamanho>")
def arquivo(token, tamanho):
    """
    Serve uma imagem da galeria. O token é aleatório e é validado antes de tocar no
    disco (impede caminhos como ../). Imagens de espécies ocultas só são entregues a
    administradores logados; para o público respondem 404, como se não existissem.
    """
    local = armazem.localizar(token, tamanho)
    if local is None:
        abort(404)

    visivel = db.imagem_visivel(token)
    if visivel is None or (not visivel and g.get("usuario") is None):
        abort(404)

    pasta, nome = local
    resposta = send_from_directory(pasta, nome, mimetype="image/webp", conditional=True)
    resposta.headers["Cache-Control"] = (
        "public, max-age=86400" if visivel else "private, no-store"
    )
    # Defesa em profundidade: mesmo que algo fosse interpretado como página, não executa nada
    resposta.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resposta.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return resposta