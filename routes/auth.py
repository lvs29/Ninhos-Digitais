from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

import db
from security import destino_seguro, login_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.usuario:
        return redirect(url_for("admin.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        senha = request.form.get("senha", "")

        usuario = db.autenticar_usuario(username, senha)
        if usuario:
            session.clear()  # novo id de sessão a cada login
            session["usuario_id"] = usuario["id"]
            session.permanent = True

            if username == "admin" and senha == "admin":
                flash("Você está usando a senha padrão. Troque-a agora.", "aviso")
                return redirect(url_for("auth.alterar_senha"))

            return redirect(destino_seguro(request.args.get("next"), url_for("admin.index")))

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

        if nova != confirmar:
            flash("A confirmação não confere com a nova senha.", "erro")
        else:
            try:
                db.alterar_senha(g.usuario["id"], atual, nova)
            except ValueError as e:
                flash(str(e), "erro")
            else:
                flash("Senha alterada.", "ok")
                return redirect(url_for("admin.index"))

    return render_template("senha.html")