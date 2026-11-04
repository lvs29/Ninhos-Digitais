import os

from flask import (
    Blueprint, Response, abort, current_app, flash, g, redirect,
    render_template, request, url_for,
)

import transferencia
from security import exigir_login

config_bp = Blueprint("configuracoes", __name__, url_prefix="/admin/configuracoes")


@config_bp.before_request
def _exigir_login():
    """Todas as rotas de configurações exigem login."""
    return exigir_login()


@config_bp.app_errorhandler(413)
def _arquivo_grande_demais(_erro):
    limite_mb = (current_app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024)
    if g.get("usuario"):
        flash(f"Arquivo grande demais (o limite do envio é {limite_mb} MB).", "erro")
        return redirect(url_for("configuracoes.index"))
    return "Arquivo grande demais.", 413


def _pagina(resultados=None, simulacao=False):
    return render_template(
        "admin/configuracoes.html",
        tipos=transferencia.TIPOS,
        contagens=transferencia.contagens(),
        resultados=resultados,
        simulacao=simulacao,
        max_arquivos=len(transferencia.TIPOS),
    )


def _download(corpo: bytes, nome: str, mimetype: str) -> Response:
    resposta = Response(corpo, mimetype=mimetype)
    resposta.headers["Content-Disposition"] = f'attachment; filename="{nome}"'
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Cache-Control"] = "no-store"  # o arquivo pode conter dados sensíveis
    return resposta


@config_bp.route("/")
def index():
    return _pagina()


@config_bp.route("/exportar/<tipo>")
def exportar(tipo):
    if tipo not in transferencia.TIPOS:
        abort(404)
    corpo = transferencia.serializar(transferencia.exportar(tipo))
    current_app.logger.info("Exportação de '%s' por %s", tipo, g.usuario["username"])
    return _download(corpo, transferencia.nome_arquivo(tipo), "application/json")


@config_bp.route("/exportar-tudo")
def exportar_tudo():
    corpo, nome = transferencia.exportar_zip()
    current_app.logger.info("Exportação completa por %s", g.usuario["username"])
    return _download(corpo, nome, "application/zip")


@config_bp.route("/importar", methods=["POST"])
def importar():
    arquivos = []
    for enviado in request.files.getlist("arquivos"):
        if enviado and enviado.filename:
            nome = os.path.basename(enviado.filename.replace("\\", "/"))[:120]
            arquivos.append((nome, enviado.read()))

    if not arquivos:
        flash("Selecione ao menos um arquivo .json para importar.", "erro")
        return redirect(url_for("configuracoes.index"))
    if len(arquivos) > len(transferencia.TIPOS):
        flash(f"Envie no máximo {len(transferencia.TIPOS)} arquivos por vez (um de cada tipo).", "erro")
        return redirect(url_for("configuracoes.index"))

    simular = request.form.get("simular") == "on"
    resultados = transferencia.importar(arquivos, simular=simular)

    current_app.logger.info(
        "Importação%s por %s: %s", " (simulação)" if simular else "", g.usuario["username"],
        "; ".join(f"{r['arquivo']} -> {r['status']}" for r in resultados),
    )
    return _pagina(resultados, simulacao=simular)