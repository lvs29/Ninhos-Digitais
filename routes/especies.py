# routes/especies.py
import json

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

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


def _obter_especie_publica(codigo: str, especie_id: int) -> tuple[dict, dict]:
    """Valida tag e espécie visível vinculada ao código; aborta com 404 se inválida."""
    if len(codigo) > 64:
        abort(404)
    tag = db.obter_tag_por_codigo(codigo)
    if not tag:
        abort(404)

    especies = db.listar_especies_por_codigo(codigo, apenas_visiveis=True)
    especie = next((e for e in especies if e["id"] == especie_id), None)
    if especie is None:
        abort(404)

    return tag, especie


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


@especies_bp.route("/e/<codigo>/quiz/<int(max=2147483647):especie_id>")
def pagina_quiz(codigo, especie_id):
    tag, especie = _obter_especie_publica(codigo, especie_id)
    quiz = especie.get("quiz")
    if not quiz or not quiz.get("perguntas"):
        abort(404)

    return render_template("quiz.html", especie=especie, codigo=codigo, tag=tag)


@especies_bp.route("/e/<codigo>/quiz/<int(max=2147483647):especie_id>", methods=["POST"])
def responder_quiz_publico(codigo, especie_id):
    _obter_especie_publica(codigo, especie_id)

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        respostas = payload.get("respostas")
        tempo_total_ms = payload.get("tempo_total_ms", 0)
    else:
        try:
            respostas = json.loads(request.form.get("respostas") or "[]")
        except json.JSONDecodeError:
            return jsonify({"ok": False, "erro": "Formato de respostas inválido."}), 400
        try:
            tempo_total_ms = int(request.form.get("tempo_total_ms") or 0)
        except ValueError:
            tempo_total_ms = 0

    # Admin logado testando a página não infla as métricas públicas
    if g.get("usuario"):
        quiz = db.obter_quiz_especie(especie_id)
        perguntas = (quiz or {}).get("perguntas", [])
        qtd_esperada = min(len(perguntas), db.MAX_PERGUNTAS_QUIZ_PUBLICO)
        acertos = 0
        if isinstance(respostas, list) and len(respostas) == qtd_esperada:
            for idx, item in enumerate(respostas):
                p_idx = item.get("pergunta_idx", idx) if isinstance(item, dict) else idx
                alt_idx = item if (isinstance(item, int) and not isinstance(item, bool)) else (
                    item.get("alternativa_idx") if isinstance(item, dict) else None
                )
                if isinstance(p_idx, int) and not isinstance(p_idx, bool) and 0 <= p_idx < len(perguntas):
                    alts = perguntas[p_idx].get("alternativas", [])
                    if (
                        isinstance(alt_idx, int)
                        and not isinstance(alt_idx, bool)
                        and 0 <= alt_idx < len(alts)
                        and alts[alt_idx].get("correta")
                    ):
                        acertos += 1
        return jsonify({
            "ok": True,
            "ignorado_admin": True,
            "acertos": acertos,
            "total_perguntas": qtd_esperada,
        })

    try:
        resultado = db.registrar_resposta_quiz(especie_id, respostas, tempo_total_ms)
    except ValueError as e:
        return jsonify({"ok": False, "erro": str(e)}), 400
    except Exception:
        current_app.logger.exception("Falha ao salvar resposta do quiz da espécie %s", especie_id)
        return jsonify({"ok": False, "erro": "Erro interno ao registrar resposta."}), 500

    return jsonify({"ok": True, **resultado})