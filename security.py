"""Sessão, proteção CSRF, limites de requisição, cabeçalhos de segurança e decoradores de acesso."""
import hmac
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, abort, flash, g, redirect, request, session, url_for

import db

METODOS_PROTEGIDOS = {"POST", "PUT", "PATCH", "DELETE"}

KB = 1024
MB = 1024 * KB

# Tamanho máximo do CORPO de cada requisição. A regra geral é pequena: formulários comuns
# nunca precisam de mais que isso. Só os endpoints de envio de arquivos têm limite maior,
# e isso só vale para usuários logados (anônimos nunca conseguem enviar corpos grandes).
LIMITE_PADRAO = 256 * KB
LIMITES_UPLOAD = {
    "admin.especie_nova": 32 * MB,        # até 6 imagens de 5 MB + campos do formulário
    "admin.especie_editar": 32 * MB,
    "configuracoes.importar": 21 * MB,    # arquivos JSON de importação (até 20 MB)
}


class LimitadorTentativas:
    """
    Limita tentativas com falha por chave, numa janela deslizante (em memória).
    Observações: o contador é por processo (reinicia ao reiniciar o servidor e não é
    compartilhado entre vários workers). Atrás de um proxy reverso, configure o proxy
    para repassar o IP real (ex.: werkzeug ProxyFix); senão todos parecem ter o mesmo IP.
    """

    def __init__(self, max_falhas: int, janela: int):
        self.max_falhas = max_falhas
        self.janela = janela  # segundos
        self._falhas: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recentes(self, chave: str, agora: float) -> list[float]:
        lista = [t for t in self._falhas.get(chave, []) if agora - t < self.janela]
        if lista:
            self._falhas[chave] = lista
        else:
            self._falhas.pop(chave, None)
        return lista

    def espera(self, chave: str) -> int:
        """Segundos até poder tentar de novo (0 = liberado)."""
        agora = _agora()
        with self._lock:
            recentes = self._recentes(chave, agora)
            if len(recentes) >= self.max_falhas:
                return int(self.janela - (agora - recentes[0])) + 1
            return 0

    def falha(self, chave: str) -> None:
        agora = _agora()
        with self._lock:
            if len(self._falhas) > 10_000:  # evita crescer sem limite
                for k in list(self._falhas):
                    self._recentes(k, agora)
            self._recentes(chave, agora)
            self._falhas.setdefault(chave, []).append(agora)

    def limpar(self, chave: str) -> None:
        with self._lock:
            self._falhas.pop(chave, None)


_agora = time.monotonic  # trocável nos testes

_login_por_usuario = LimitadorTentativas(max_falhas=5, janela=15 * 60)    # IP + login
_login_por_ip = LimitadorTentativas(max_falhas=30, janela=15 * 60)        # qualquer login do IP
_troca_de_senha = LimitadorTentativas(max_falhas=5, janela=15 * 60)       # por usuário logado


def _chave_login(ip: str, username: str) -> str:
    return f"{ip}|{username.strip().lower()[:100]}"


def login_espera(ip: str, username: str) -> int:
    """Segundos de bloqueio restantes para esta tentativa de login (0 = pode tentar)."""
    return max(_login_por_ip.espera(ip), _login_por_usuario.espera(_chave_login(ip, username)))


def login_falhou(ip: str, username: str) -> None:
    _login_por_ip.falha(ip)
    _login_por_usuario.falha(_chave_login(ip, username))


def login_ok(ip: str, username: str) -> None:
    _login_por_usuario.limpar(_chave_login(ip, username))


def troca_senha_espera(usuario_id: int) -> int:
    return _troca_de_senha.espera(str(usuario_id))


def troca_senha_falhou(usuario_id: int) -> None:
    _troca_de_senha.falha(str(usuario_id))


def troca_senha_ok(usuario_id: int) -> None:
    _troca_de_senha.limpar(str(usuario_id))


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

        # Limite do corpo da requisição (precisa vir ANTES de qualquer leitura de request.form).
        # Corpos maiores que o limite viram 413 sem serem processados.
        limite = LIMITE_PADRAO
        if g.usuario is not None:
            limite = LIMITES_UPLOAD.get(request.endpoint, LIMITE_PADRAO)
        request.max_content_length = limite

        # CSRF em toda requisição que altera estado
        if request.method in METODOS_PROTEGIDOS:
            esperado = session.get("_csrf", "")
            enviado = request.form.get("csrf_token", "")
            if not esperado or not hmac.compare_digest(enviado, esperado):
                abort(400)

    @app.errorhandler(413)
    def _envio_grande_demais(_erro):
        """Corpo acima do limite: usuário logado volta ao formulário com um aviso."""
        if g.get("usuario") is not None and request.method == "POST":
            flash("O envio ultrapassou o tamanho máximo permitido. "
                  "Reduza o tamanho ou a quantidade dos arquivos.", "erro")
            if request.endpoint in LIMITES_UPLOAD:
                if request.endpoint.startswith("configuracoes."):
                    return redirect(url_for("configuracoes.index"))
                return redirect(request.path)  # volta para o mesmo formulário
        return "Envio grande demais.", 413

    @app.after_request
    def _cabecalhos_de_seguranca(resposta):
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")          # anti-clickjacking
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        return resposta

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