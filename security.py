"""Sessão, proteção CSRF e decoradores de acesso."""
import hmac
import os
import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, abort, g, redirect, request, session, url_for

import db

METODOS_PROTEGIDOS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def init_security(app: Flask) -> None:
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Defina COOKIE_SECURE=1 em produção (site em HTTPS)
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _carregar_usuario_e_checar_csrf():
        # Usuário logado: sempre relido do banco (remoção de usuário vale na hora)
        g.usuario = None
        usuario_id = session.get("usuario_id")
        if usuario_id is not None:
            g.usuario = db.buscar_usuario(usuario_id)
            if g.usuario is None:
                session.clear()

        # CSRF em toda requisição que altera estado
        if request.method in METODOS_PROTEGIDOS:
            esperado = session.get("_csrf", "")
            enviado = request.form.get("csrf_token", "")
            if not esperado or not hmac.compare_digest(enviado, esperado):
                abort(400)

    @app.context_processor
    def _injetar_usuario():
        return {"usuario_atual": g.get("usuario")}


def destino_seguro(destino: str | None, padrao: str) -> str:
    """Evita open redirect: só aceita caminhos internos (ex.: /admin/especies)."""
    if not destino:
        return padrao
    partes = urlparse(destino)
    if partes.scheme or partes.netloc or not destino.startswith("/") \
            or destino.startswith("//") or "\\" in destino:
        return padrao
    return destino


def exigir_login():
    """Retorna um redirect para /login se não houver usuário logado; senão None."""
    if g.usuario is None:
        if request.method == "GET":
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return redirect(url_for("auth.login"))
    return None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        resposta = exigir_login()
        if resposta is not None:
            return resposta
        return f(*args, **kwargs)
    return wrapper