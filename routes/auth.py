from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

import db
import security
from security import destino_seguro, login_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.usuario:
        return redirect(url_for("admin.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()[:200]
        senha = request.form.get("senha", "")
        ip = request.remote_addr or "desconhecido"

        # Proteção contra força bruta: bloqueia antes mesmo de verificar a senha
        espera = security.login_espera(ip, username)
        if espera:
            minutos = max(1, round(espera / 60))
            flash(f"Muitas tentativas de login. Tente novamente em cerca de {minutos} minuto(s).", "erro")
            return render_template("login.html"), 429

        usuario = db.autenticar_usuario(username, senha)
        if usuario:
            security.login_ok(ip, username)
            session.clear()  # novo id de sessão a cada login
            session["usuario_id"] = usuario["id"]
            session.permanent = True

            if username == "admin" and senha == "admin":
                flash("Você está usando a senha padrão. Troque-a agora.", "aviso")
                return redirect(url_for("auth.alterar_senha"))

            return redirect(destino_seguro(request.args.get("next"), url_for("admin.index")))

        security.login_falhou(ip, username)
        flash("Usuário ou senha incorretos.", "erro")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Você saiu.", "ok")
    return redirect(url_for("main.index"))


@auth_bp.route("/conta/senha", methods=["GET", "POST"])
@login_required
def alterar_senha():
    if request.method == "POST":
        atual = request.form.get("senha_atual", "")
        nova = request.form.get("nova_senha", "")
        confirmar = request.form.get("confirmar", "")

        espera = security.troca_senha_espera(g.usuario["id"])
        if espera:
            flash(f"Muitas tentativas. Tente novamente em cerca de {max(1, round(espera / 60))} minuto(s).", "erro")
        elif nova != confirmar:
            flash("A confirmação não confere com a nova senha.", "erro")
        else:
            try:
                db.alterar_senha(g.usuario["id"], atual, nova)
            except db.SenhaAtualIncorreta as e:
                security.troca_senha_falhou(g.usuario["id"])
                flash(str(e), "erro")
            except ValueError as e:
                flash(str(e), "erro")
            else:
                security.troca_senha_ok(g.usuario["id"])
                flash("Senha alterada.", "ok")
                return redirect(url_for("admin.index"))

    return render_template("senha.html")