from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.routing import BuildError

import db
from security import exigir_login

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def _exigir_login():
    """Toda rota deste blueprint exige login (rotas novas já nascem protegidas)."""
    return exigir_login()


def _link_publico(codigo: str) -> str:
    """URL pública da tag (a mesma para QR code, NFC, etc.)."""
    try:
        return url_for("especies.pagina_publica", codigo=codigo, _external=True)
    except BuildError:  # blueprint/endpoint com outro nome
        return url_for("main.index", _external=True).rstrip("/") + f"/e/{codigo}"


def _id_opcional(valor: str | None) -> int | None:
    """Converte o valor de um <select> ('' = nenhum) em int ou None."""
    valor = (valor or "").strip()
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        raise ValueError("Seleção inválida.")


@admin_bp.route("/")
def index():
    return redirect(url_for("admin.especies_lista"))


# ─── Espécies ─────────────────────────────────────────────────────────────────

def _form_especie(valores, novo, especie_id=None):
    """Renderiza o formulário de espécie com a lista de tags que podem ser escolhidas."""
    tags = [
        t for t in db.listar_tags()
        if t["especie"] is None or t["especie"]["id"] == especie_id
    ]
    return render_template(
        "admin/especie_form.html",
        especie=valores, novo=novo, tags=tags,
        tag_id_atual=str(valores.get("tag_id") or ""),
    )


def _valores_form_especie() -> dict:
    f = request.form
    return {
        "nome": f.get("nome", "").strip(),
        "nome_cientifico": f.get("nome_cientifico", "").strip(),
        "distribuicao_geografica": f.get("distribuicao_geografica", "").strip(),
        "ameacada": f.get("ameacada") == "on",
        "tag_id": f.get("tag_id", ""),
    }


@admin_bp.route("/especies")
def especies_lista():
    busca = request.args.get("q", "").strip()
    filtro = request.args.get("ameacada")
    ameacada = {"1": True, "0": False}.get(filtro)

    especies = db.listar_especies(busca=busca or None, ameacada=ameacada)
    for e in especies:
        e["url"] = _link_publico(e["tag"]["codigo"]) if e["tag"] else None
    return render_template("admin/especies.html", especies=especies, busca=busca, filtro=filtro)


@admin_bp.route("/especies/nova", methods=["GET", "POST"])
def especie_nova():
    if request.method == "POST":
        valores = _valores_form_especie()
        try:
            dados = {**valores, "tag_id": _id_opcional(valores["tag_id"])}
            db.criar_especie(**dados)
        except ValueError as e:
            flash(str(e), "erro")
            return _form_especie(valores, novo=True)
        flash("Espécie cadastrada.", "ok")
        return redirect(url_for("admin.especies_lista"))

    return _form_especie({}, novo=True)


@admin_bp.route("/especies/<int:especie_id>/editar", methods=["GET", "POST"])
def especie_editar(especie_id):
    if request.method == "POST":
        valores = _valores_form_especie()
        try:
            dados = {**valores, "tag_id": _id_opcional(valores["tag_id"])}
            atualizada = db.atualizar_especie(especie_id, **dados)
        except ValueError as e:
            flash(str(e), "erro")
            return _form_especie(valores, novo=False, especie_id=especie_id)
        if atualizada is None:
            abort(404)
        flash("Espécie atualizada.", "ok")
        return redirect(url_for("admin.especies_lista"))

    especie = db.obter_especie(especie_id)
    if especie is None:
        abort(404)
    especie["tag_id"] = especie["tag"]["id"] if especie["tag"] else ""
    return _form_especie(especie, novo=False, especie_id=especie_id)


@admin_bp.route("/especies/<int:especie_id>/remover", methods=["POST"])
def especie_remover(especie_id):
    if not db.remover_especie(especie_id):
        abort(404)
    flash("Espécie removida.", "ok")
    return redirect(url_for("admin.especies_lista"))


# ─── Tags ─────────────────────────────────────────────────────────────────────

def _form_tag(valores, novo, tag_id=None, codigo=None):
    """Renderiza o formulário de tag com a lista de espécies que podem ser escolhidas."""
    especies = [
        e for e in db.listar_especies()
        if e["tag"] is None or e["tag"]["id"] == tag_id
    ]
    return render_template(
        "admin/tag_form.html",
        tag=valores, novo=novo, especies=especies, codigo=codigo,
        url=_link_publico(codigo) if codigo else None,
        especie_id_atual=str(valores.get("especie_id") or ""),
    )


@admin_bp.route("/tags")
def tags_lista():
    tags = db.listar_tags()
    for t in tags:
        t["url"] = _link_publico(t["codigo"])
    return render_template("admin/tags.html", tags=tags)


@admin_bp.route("/tags/nova", methods=["GET", "POST"])
def tag_nova():
    if request.method == "POST":
        valores = {
            "nome": request.form.get("nome", "").strip(),
            "especie_id": request.form.get("especie_id", ""),
        }
        try:
            db.criar_tag(valores["nome"], especie_id=_id_opcional(valores["especie_id"]))
        except ValueError as e:
            flash(str(e), "erro")
            return _form_tag(valores, novo=True)
        flash("Tag criada. O código dela é fixo e já pode ser gravado/impresso.", "ok")
        return redirect(url_for("admin.tags_lista"))

    return _form_tag({}, novo=True)


@admin_bp.route("/tags/<int:tag_id>/editar", methods=["GET", "POST"])
def tag_editar(tag_id):
    tag = db.obter_tag(tag_id)
    if tag is None:
        abort(404)

    if request.method == "POST":
        valores = {
            "nome": request.form.get("nome", "").strip(),
            "especie_id": request.form.get("especie_id", ""),
        }
        try:
            db.atualizar_tag(
                tag_id, nome=valores["nome"],
                especie_id=_id_opcional(valores["especie_id"]),
            )
        except ValueError as e:
            flash(str(e), "erro")
            return _form_tag(valores, novo=False, tag_id=tag_id, codigo=tag["codigo"])
        flash("Tag atualizada.", "ok")
        return redirect(url_for("admin.tags_lista"))

    tag["especie_id"] = tag["especie"]["id"] if tag["especie"] else ""
    return _form_tag(tag, novo=False, tag_id=tag_id, codigo=tag["codigo"])


@admin_bp.route("/tags/<int:tag_id>/remover", methods=["POST"])
def tag_remover(tag_id):
    if not db.remover_tag(tag_id):
        abort(404)
    flash("Tag removida.", "ok")
    return redirect(url_for("admin.tags_lista"))


# ─── Usuários (todos são administradores) ─────────────────────────────────────

@admin_bp.route("/usuarios")
def usuarios_lista():
    return render_template("admin/usuarios.html", usuarios=db.listar_usuarios())


@admin_bp.route("/usuarios/novo", methods=["GET", "POST"])
def usuario_novo():
    if request.method == "POST":
        nome = request.form.get("nome", "")
        username = request.form.get("username", "")
        senha = request.form.get("senha", "")
        try:
            db.criar_usuario(nome, username, senha)
        except ValueError as e:
            flash(str(e), "erro")
            return render_template("admin/usuario_form.html",
                                   dados={"nome": nome, "username": username})
        flash("Usuário criado.", "ok")
        return redirect(url_for("admin.usuarios_lista"))

    return render_template("admin/usuario_form.html", dados=None)


@admin_bp.route("/usuarios/<int:usuario_id>/remover", methods=["POST"])
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