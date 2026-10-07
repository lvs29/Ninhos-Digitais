from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.routing import BuildError

import db
from security import admin_required, login_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _link_publico(codigo: str) -> str:
    """URL pública da espécie (a mesma para QR code, NFC, etc.)."""
    try:
        return url_for("especies.pagina_publica", codigo=codigo, _external=True)
    except BuildError:  # blueprint/endpoint com outro nome
        return url_for("main.index", _external=True).rstrip("/") + f"/e/{codigo}"


def _dados_especie() -> dict:
    f = request.form
    return {
        "nome": f.get("nome", "").strip(),
        "nome_cientifico": f.get("nome_cientifico", "").strip(),
        "distribuicao_geografica": f.get("distribuicao_geografica", "").strip(),
        "ameacada": f.get("ameacada") == "on",
    }


@admin_bp.route("/")
@login_required
def index():
    return redirect(url_for("admin.especies_lista"))


# ─── Espécies (qualquer usuário logado) ───────────────────────────────────────

@admin_bp.route("/especies")
@login_required
def especies_lista():
    busca = request.args.get("q", "").strip()
    filtro = request.args.get("ameacada")
    ameacada = {"1": True, "0": False}.get(filtro)

    especies = db.listar_especies(busca=busca or None, ameacada=ameacada)
    for e in especies:
        e["url"] = _link_publico(e["codigo"])
    return render_template("admin/especies.html", especies=especies, busca=busca, filtro=filtro)


@admin_bp.route("/especies/nova", methods=["GET", "POST"])
@login_required
def especie_nova():
    if request.method == "POST":
        dados = _dados_especie()
        try:
            db.criar_especie(**dados)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("admin/especie_form.html", especie=dados, novo=True)
        flash("Espécie cadastrada.", "ok")
        return redirect(url_for("admin.especies_lista"))

    return render_template("admin/especie_form.html", especie=None, novo=True)


@admin_bp.route("/especies/<int:especie_id>/editar", methods=["GET", "POST"])
@login_required
def especie_editar(especie_id):
    if request.method == "POST":
        dados = _dados_especie()
        try:
            atualizada = db.atualizar_especie(especie_id, **dados)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("admin/especie_form.html", especie=dados, novo=False)
        if atualizada is None:
            abort(404)
        flash("Espécie atualizada.", "ok")
        return redirect(url_for("admin.especies_lista"))

    especie = db.obter_especie(especie_id)
    if especie is None:
        abort(404)
    return render_template("admin/especie_form.html", especie=especie, novo=False)


@admin_bp.route("/especies/<int:especie_id>/remover", methods=["POST"])
@login_required
def especie_remover(especie_id):
    if not db.remover_especie(especie_id):
        abort(404)
    flash("Espécie removida.", "ok")
    return redirect(url_for("admin.especies_lista"))


@admin_bp.route("/especies/<int:especie_id>/novo-codigo", methods=["POST"])
@login_required
def especie_novo_codigo(especie_id):
    if db.regenerar_codigo(especie_id) is None:
        abort(404)
    flash("Novo link gerado. QR codes e tags NFC antigos deixam de funcionar.", "aviso")
    return redirect(url_for("admin.especies_lista"))


# ─── Usuários (somente admin) ─────────────────────────────────────────────────

@admin_bp.route("/usuarios")
@admin_required
def usuarios_lista():
    return render_template("admin/usuarios.html", usuarios=db.listar_usuarios())


@admin_bp.route("/usuarios/novo", methods=["GET", "POST"])
@admin_required
def usuario_novo():
    if request.method == "POST":
        nome = request.form.get("nome", "")
        username = request.form.get("username", "")
        senha = request.form.get("senha", "")
        role = request.form.get("role", "user")
        try:
            db.criar_usuario(nome, username, senha, role=role)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("admin/usuario_form.html",
                                   dados={"nome": nome, "username": username, "role": role})
        flash("Usuário criado.", "ok")
        return redirect(url_for("admin.usuarios_lista"))

    return render_template("admin/usuario_form.html", dados=None)


@admin_bp.route("/usuarios/<int:usuario_id>/remover", methods=["POST"])
@admin_required
def usuario_remover(usuario_id):
    if usuario_id == g.usuario["id"]:
        flash("Você não pode remover a si mesmo.", "erro")
        return redirect(url_for("admin.usuarios_lista"))
    try:
        if not db.remover_usuario(usuario_id):
            abort(404)
    except ValueError as e:
        flash(str(e), "erro")
        return redirect(url_for("admin.usuarios_lista"))
    flash("Usuário removido.", "ok")
    return redirect(url_for("admin.usuarios_lista"))