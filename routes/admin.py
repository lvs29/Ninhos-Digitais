import json
import re
from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.routing import BuildError

import db
import imagens as armazem
from security import exigir_login

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

MAX_ALTERNATIVAS_QUIZ = 5


@admin_bp.before_request
def _exigir_login():
    """Toda rota deste blueprint exige login (rotas novas já nascem protegidas)."""
    return exigir_login()


@admin_bp.app_template_filter("data_hora")
def _data_hora(iso: str | None) -> str:
    """Formata datas ISO (guardadas em UTC) para exibição."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M") + " UTC"
    except ValueError:
        return iso


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
    return _inteiro(valor)


def _inteiro(valor: str) -> int:
    """Inteiro positivo de até 32 bits (o que cabe com folga em qualquer id do banco)."""
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise ValueError("Seleção inválida.")
    if not 0 < n <= 2147483647:
        raise ValueError("Seleção inválida.")
    return n


def _ids(valores: list[str], maximo: int = 2000) -> list[int]:
    if len(valores) > maximo:
        raise ValueError("Seleção inválida.")
    return [_inteiro(v) for v in valores]


def _validar_e_normalizar_quiz(raw_json: str | None) -> dict:
    """Valida estrutura do quiz: 1+ perguntas, 2 a 5 alternativas por pergunta, exatamente 1 correta."""
    try:
        payload = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        raise ValueError("Dados do quiz inválidos.")

    if not isinstance(payload, dict):
        raise ValueError("Formato do quiz inválido.")

    titulo = str(payload.get("titulo") or "").strip()
    if len(titulo) > db.MAX_NOME:
        raise ValueError(f"O título do quiz deve ter no máximo {db.MAX_NOME} caracteres.")

    perguntas_raw = payload.get("perguntas")
    if not isinstance(perguntas_raw, list) or len(perguntas_raw) == 0:
        raise ValueError("Adicione pelo menos uma pergunta ao quiz.")

    perguntas_normalizadas = []
    for idx, p in enumerate(perguntas_raw, start=1):
        if not isinstance(p, dict):
            raise ValueError(f"Pergunta {idx} inválida.")

        enunciado = str(p.get("enunciado") or "").strip()
        if not enunciado:
            raise ValueError(f"O enunciado da pergunta {idx} é obrigatório.")

        alts_raw = p.get("alternativas")
        if not isinstance(alts_raw, list) or not (2 <= len(alts_raw) <= MAX_ALTERNATIVAS_QUIZ):
            raise ValueError(
                f"A pergunta {idx} deve ter entre 2 e {MAX_ALTERNATIVAS_QUIZ} alternativas."
            )

        alternativas_normalizadas = []
        qtd_corretas = 0
        for a_idx, alt in enumerate(alts_raw, start=1):
            if not isinstance(alt, dict):
                raise ValueError(f"Alternativa {a_idx} da pergunta {idx} inválida.")
            texto = str(alt.get("texto") or "").strip()
            if not texto:
                raise ValueError(f"O texto da alternativa {a_idx} da pergunta {idx} é obrigatório.")
            correta = bool(alt.get("correta"))
            if correta:
                qtd_corretas += 1
            alternativas_normalizadas.append({"texto": texto, "correta": correta})

        if qtd_corretas != 1:
            raise ValueError(
                f"A pergunta {idx} deve ter exatamente 1 alternativa correta."
            )

        perguntas_normalizadas.append({
            "enunciado": enunciado,
            "alternativas": alternativas_normalizadas,
        })

    return {"titulo": titulo, "perguntas": perguntas_normalizadas}


@admin_bp.route("/")
def index():
    return redirect(url_for("admin.especies_lista"))


# ─── Espécies ─────────────────────────────────────────────────────────────────

def _form_especie(valores, novo, especie_id=None):
    imagens = []
    if especie_id is not None:
        atual = db.obter_especie(especie_id)
        imagens = atual["imagens"] if atual else []
    return render_template(
        "admin/especie_form.html",
        especie=valores, novo=novo,
        categorias=db.CATEGORIAS_AMEACA,
        tags=db.listar_tags(),
        tag_id_atual=str(valores.get("tag_id") or ""),
        imagens=imagens,
        limites={
            "por_envio": armazem.MAX_POR_ENVIO,
            "mb": armazem.MAX_BYTES // (1024 * 1024),
            "por_especie": armazem.MAX_POR_ESPECIE,
            "restantes": max(0, armazem.MAX_POR_ESPECIE - len(imagens)),
            "legenda": armazem.MAX_LEGENDA,
        },
    )


RE_AJUSTE_IMAGEM = re.compile(r"(ordem|legenda)_(\d{1,10})")


def _galeria_enviada() -> tuple[list[int], dict[int, dict]]:
    """
    Lê do formulário de edição as imagens marcadas para remover e os ajustes de
    ordem/legenda (campos ordem_<id> e legenda_<id>). O banco ignora ids que não
    pertencem à espécie.
    """
    remover = _ids(request.form.getlist("remover_imagens"), maximo=100)
    ajustes: dict[int, dict] = {}
    for chave in request.form.keys():
        achou = RE_AJUSTE_IMAGEM.fullmatch(chave)
        if not achou:
            continue
        campo, id_imagem = achou.group(1), int(achou.group(2))
        valor = request.form.get(chave, "")
        if campo == "ordem":
            if valor.strip():
                ajustes.setdefault(id_imagem, {})["ordem"] = valor.strip()
        else:
            ajustes.setdefault(id_imagem, {})["legenda"] = valor
    return remover, ajustes


def _valores_form_especie() -> dict:
    """Valores crus do formulário (strings), usados para reexibir o form em caso de erro."""
    f = request.form
    valores = {
        "nome": f.get("nome", "").strip(),
        "nome_cientifico": f.get("nome_cientifico", "").strip(),
        "distribuicao_geografica": f.get("distribuicao_geografica", "").strip(),
        "categoria_ameaca": f.get("categoria_ameaca", ""),
        "visivel": f.get("visivel") == "on",
        "tag_id": f.get("tag_id", ""),
    }
    for campo in db.CAMPOS_OPCIONAIS:
        valores[campo] = f.get(campo, "").strip()
    return valores


@admin_bp.route("/especies")
def especies_lista():
    busca = request.args.get("q", "").strip()[:100]
    categoria = request.args.get("categoria", "")
    visivel = request.args.get("visivel", "")

    filtros = {"busca": busca or None, "visivel": {"1": True, "0": False}.get(visivel)}
    if categoria == "ameacadas":
        filtros["ameacada"] = True
    elif categoria in db.CATEGORIAS_AMEACA:
        filtros["categoria"] = categoria

    especies = db.listar_especies(**filtros)
    for e in especies:
        if e.get("tag"):
            e["url"] = _link_publico(e["tag"]["codigo"])
        elif e.get("tags"):
            e["url"] = _link_publico(e["tags"][0]["codigo"])
        else:
            e["url"] = None

    return render_template(
        "admin/especies.html",
        especies=especies,
        categorias=db.CATEGORIAS_AMEACA,
        busca=busca, categoria=categoria, visivel=visivel,
    )


@admin_bp.route("/especies/nova", methods=["GET", "POST"])
def especie_nova():
    if request.method == "POST":
        valores = _valores_form_especie()
        try:
            novas = armazem.processar_envio(request.files.getlist("imagens"))
            db.criar_especie(**{**valores, "tag_id": _id_opcional(valores["tag_id"]), "imagens": novas})
        except ValueError as e:
            flash(str(e), "erro")
            return _form_especie(valores, novo=True)
        flash("Espécie cadastrada.", "ok")
        return redirect(url_for("admin.especies_lista"))

    return _form_especie({"categoria_ameaca": "NE", "visivel": True}, novo=True)


@admin_bp.route("/especies/<int(max=2147483647):especie_id>/editar", methods=["GET", "POST"])
def especie_editar(especie_id):
    if request.method == "POST":
        valores = _valores_form_especie()
        try:
            novas = armazem.processar_envio(request.files.getlist("imagens"))
            remover, ajustes = _galeria_enviada()
            atualizada = db.atualizar_especie(
                especie_id,
                **{**valores, "tag_id": _id_opcional(valores["tag_id"]),
                   "imagens_novas": novas, "imagens_remover": remover, "imagens_ajustes": ajustes},
            )
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
    especie["tag_id"] = especie["tag"]["id"] if especie.get("tag") else ""
    return _form_especie(especie, novo=False, especie_id=especie_id)


@admin_bp.route("/especies/<int(max=2147483647):especie_id>/visibilidade", methods=["POST"])
def especie_visibilidade(especie_id):
    """Alterna a visibilidade pública sem apagar a espécie."""
    especie = db.obter_especie(especie_id)
    if especie is None:
        abort(404)
    db.atualizar_especie(especie_id, visivel=not especie["visivel"])
    flash(
        f"'{especie['nome']}' agora está "
        + ("visível ao público." if not especie["visivel"] else "oculta ao público."),
        "ok",
    )
    return redirect(request.referrer or url_for("admin.especies_lista"))


@admin_bp.route("/especies/<int(max=2147483647):especie_id>/remover", methods=["POST"])
def especie_remover(especie_id):
    if not db.remover_especie(especie_id):
        abort(404)
    flash("Espécie removida.", "ok")
    return redirect(url_for("admin.especies_lista"))


@admin_bp.route("/especies/<int(max=2147483647):especie_id>/quiz", methods=["GET", "POST"])
def especie_quiz(especie_id):
    especie = db.obter_especie(especie_id)
    if especie is None:
        abort(404)

    quiz_atual = db.obter_quiz_especie(especie_id) or {}
    stats = db.estatisticas_quiz_especie(especie_id)

    if request.method == "POST":
        raw_json = request.form.get("quiz_json")
        try:
            quiz_validado = _validar_e_normalizar_quiz(raw_json)
            db.salvar_quiz_especie(especie_id, quiz_validado)
        except ValueError as e:
            flash(str(e), "erro")
            try:
                rascunho = json.loads(raw_json or "{}")
            except json.JSONDecodeError:
                rascunho = quiz_atual
            return render_template("admin/quiz_form.html", especie=especie, quiz=rascunho, stats=stats)
        except Exception as e:
            flash(f"Erro ao salvar quiz no banco: {e}", "erro")
            try:
                rascunho = json.loads(raw_json or "{}")
            except json.JSONDecodeError:
                rascunho = quiz_atual
            return render_template("admin/quiz_form.html", especie=especie, quiz=rascunho, stats=stats)

        flash("Quiz salvo com sucesso.", "ok")
        return redirect(url_for("admin.especie_quiz", especie_id=especie_id))

    return render_template("admin/quiz_form.html", especie=especie, quiz=quiz_atual, stats=stats)


@admin_bp.route("/especies/<int(max=2147483647):especie_id>/quiz/limpar-estatisticas", methods=["POST"])
def especie_quiz_limpar_stats(especie_id):
    especie = db.obter_especie(especie_id)
    if especie is None:
        abort(404)
    qtd = db.limpar_respostas_quiz(especie_id)
    flash(f"Estatísticas zeradas ({qtd} registro(s) removido(s)).", "ok")
    return redirect(url_for("admin.especie_quiz", especie_id=especie_id))


# ─── Tags (viveiros/aquários) ─────────────────────────────────────────────────

def _form_tag(valores, novo, tag_id=None, codigo=None):
    especies = db.listar_especies()
    for e in especies:
        # Espécie que já está em OUTRA tag não pode ser escolhida aqui
        e["em_outra_tag"] = e["tag"]["nome"] if e.get("tag") and e["tag"]["id"] != tag_id else None
    return render_template(
        "admin/tag_form.html",
        tag=valores, novo=novo, especies=especies, codigo=codigo,
        url=_link_publico(codigo) if codigo else None,
        selecionadas=set(valores.get("especie_ids", [])),
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
            "especie_ids": request.form.getlist("especie_ids"),
        }
        try:
            db.criar_tag(valores["nome"], especie_ids=_ids(valores["especie_ids"]))
        except ValueError as e:
            flash(str(e), "erro")
            return _form_tag(valores, novo=True)
        flash("Tag criada. O código dela é fixo e já pode ser gravado/impresso.", "ok")
        return redirect(url_for("admin.tags_lista"))

    return _form_tag({}, novo=True)


@admin_bp.route("/tags/<int(max=2147483647):tag_id>/editar", methods=["GET", "POST"])
def tag_editar(tag_id):
    tag = db.obter_tag(tag_id)
    if tag is None:
        abort(404)

    if request.method == "POST":
        valores = {
            "nome": request.form.get("nome", "").strip(),
            "especie_ids": request.form.getlist("especie_ids"),
        }
        try:
            db.atualizar_tag(
                tag_id, nome=valores["nome"], especie_ids=_ids(valores["especie_ids"]),
            )
        except ValueError as e:
            flash(str(e), "erro")
            return _form_tag(valores, novo=False, tag_id=tag_id, codigo=tag["codigo"])
        flash("Tag atualizada.", "ok")
        return redirect(url_for("admin.tags_lista"))

    tag["especie_ids"] = [str(e["id"]) for e in tag["especies"]]
    return _form_tag(tag, novo=False, tag_id=tag_id, codigo=tag["codigo"])


@admin_bp.route("/tags/<int(max=2147483647):tag_id>/remover", methods=["POST"])
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


@admin_bp.route("/usuarios/<int(max=2147483647):usuario_id>/remover", methods=["POST"])
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