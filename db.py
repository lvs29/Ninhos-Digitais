import os
import hashlib
import hmac
import secrets
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, ForeignKey,
    DateTime, Boolean,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


# ─── Modelos ──────────────────────────────────────────────────────────────────

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    username = Column(String, nullable=False, unique=True)
    senha_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        # Nunca expõe senha_hash
        return {
            "id": self.id,
            "nome": self.nome,
            "username": self.username,
            "role": self.role,
            "criado_em": self.criado_em.isoformat(),
        }


class Especie(Base):
    __tablename__ = "especies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False, unique=True)              # Nome comum
    nome_cientifico = Column(String, nullable=False)                # Nome científico
    distribuicao_geografica = Column(Text, nullable=False)          # Distribuição geográfica
    ameacada = Column(Boolean, nullable=False, default=False)       # Ameaçada de extinção?
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    link = relationship(
        "Link",
        back_populates="especie",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "nome_cientifico": self.nome_cientifico,
            "distribuicao_geografica": self.distribuicao_geografica,
            "ameacada": self.ameacada,
            "codigo": self.link.codigo if self.link else None,
            "criado_em": self.criado_em.isoformat(),
        }


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    especie_id = Column(
        Integer,
        ForeignKey("especies.id"),
        nullable=False,
        unique=True,
    )
    # Código curto e imprevisível que vai na URL do QR code (ex.: /e/Ab3x_9QkLmw)
    codigo = Column(String, nullable=False, unique=True, default=lambda: _novo_codigo())

    especie = relationship("Especie", back_populates="link")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_db() -> Session:
    return SessionLocal()


def _novo_codigo() -> str:
    return secrets.token_urlsafe(8)


def hash_senha(senha: str) -> str:
    """PBKDF2-SHA256 com salt aleatório. Formato: salt$hash (hex)."""
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, 200_000)
    return f"{salt.hex()}${h.hex()}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        salt_hex, h_hex = senha_hash.split("$")
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    h = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, 200_000)
    return hmac.compare_digest(h.hex(), h_hex)


def _limpar(valor, campo: str) -> str:
    """Valida e normaliza um campo de texto obrigatório."""
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"O campo '{campo}' é obrigatório.")
    return valor.strip()


# ─── Usuários ─────────────────────────────────────────────────────────────────

def criar_usuario(nome: str, username: str, senha: str, role: str = "user") -> dict:
    nome = _limpar(nome, "nome")
    username = _limpar(username, "username")
    senha = _limpar(senha, "senha")
    if role not in ("user", "admin"):
        raise ValueError("role deve ser 'user' ou 'admin'.")

    db = get_db()
    try:
        usuario = Usuario(
            nome=nome,
            username=username,
            senha_hash=hash_senha(senha),
            role=role,
        )
        db.add(usuario)
        db.commit()
        return usuario.to_dict()
    except IntegrityError:
        db.rollback()
        raise ValueError(f"O usuário '{username}' já existe.")
    finally:
        db.close()


def autenticar_usuario(username: str, senha: str) -> dict | None:
    """Retorna o usuário (dict) se as credenciais estiverem corretas, senão None."""
    db = get_db()
    try:
        usuario = db.query(Usuario).filter_by(username=username).first()
        if usuario and verificar_senha(senha, usuario.senha_hash):
            return usuario.to_dict()
        return None
    finally:
        db.close()


def buscar_usuario(usuario_id: int) -> dict | None:
    db = get_db()
    try:
        usuario = db.get(Usuario, usuario_id)
        return usuario.to_dict() if usuario else None
    finally:
        db.close()


# ─── Espécies ─────────────────────────────────────────────────────────────────

def criar_especie(
    nome: str,
    nome_cientifico: str,
    distribuicao_geografica: str,
    ameacada: bool = False,
) -> dict:
    """Cria a espécie e já gera o link (código do QR code) associado."""
    nome = _limpar(nome, "nome")
    nome_cientifico = _limpar(nome_cientifico, "nome_cientifico")
    distribuicao_geografica = _limpar(distribuicao_geografica, "distribuicao_geografica")

    db = get_db()
    try:
        especie = Especie(
            nome=nome,
            nome_cientifico=nome_cientifico,
            distribuicao_geografica=distribuicao_geografica,
            ameacada=bool(ameacada),
        )
        especie.link = Link()
        db.add(especie)
        db.commit()
        return especie.to_dict()
    except IntegrityError:
        db.rollback()
        raise ValueError(f"A espécie '{nome}' já está cadastrada.")
    finally:
        db.close()


def obter_especie(especie_id: int) -> dict | None:
    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        return especie.to_dict() if especie else None
    finally:
        db.close()


def obter_especie_por_codigo(codigo: str) -> dict | None:
    """Usada pela página pública acessada via QR code."""
    db = get_db()
    try:
        link = db.query(Link).filter_by(codigo=codigo).first()
        return link.especie.to_dict() if link else None
    finally:
        db.close()


def listar_especies(busca: str | None = None, ameacada: bool | None = None) -> list[dict]:
    """
    Lista espécies em ordem alfabética.
      busca:    filtra por nome comum ou científico (contém, sem diferenciar maiúsculas)
      ameacada: True/False filtra pelo status; None traz todas
    """
    db = get_db()
    try:
        q = db.query(Especie)
        if busca and busca.strip():
            padrao = f"%{busca.strip()}%"
            q = q.filter(
                Especie.nome.ilike(padrao) | Especie.nome_cientifico.ilike(padrao)
            )
        if ameacada is not None:
            q = q.filter(Especie.ameacada == ameacada)
        return [e.to_dict() for e in q.order_by(Especie.nome).all()]
    finally:
        db.close()


def atualizar_especie(especie_id: int, **campos) -> dict | None:
    """
    Atualiza apenas os campos informados. Campos aceitos:
    nome, nome_cientifico, distribuicao_geografica, ameacada.
    Retorna a espécie atualizada ou None se o id não existir.
    """
    permitidos = {"nome", "nome_cientifico", "distribuicao_geografica", "ameacada"}
    invalidos = set(campos) - permitidos
    if invalidos:
        raise ValueError(f"Campos inválidos: {', '.join(sorted(invalidos))}")

    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        if not especie:
            return None
        for campo, valor in campos.items():
            if campo == "ameacada":
                valor = bool(valor)
            else:
                valor = _limpar(valor, campo)
            setattr(especie, campo, valor)
        db.commit()
        return especie.to_dict()
    except IntegrityError:
        db.rollback()
        raise ValueError("Já existe uma espécie com esse nome.")
    finally:
        db.close()


def remover_especie(especie_id: int) -> bool:
    """Remove a espécie e o link dela. Retorna False se o id não existir."""
    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        if not especie:
            return False
        db.delete(especie)
        db.commit()
        return True
    finally:
        db.close()


def regenerar_codigo(especie_id: int) -> str | None:
    """
    Gera um novo código para o QR code da espécie (invalida o QR antigo).
    Útil se um QR code impresso for comprometido.
    """
    db = get_db()
    try:
        link = db.query(Link).filter_by(especie_id=especie_id).first()
        if not link:
            return None
        link.codigo = _novo_codigo()
        db.commit()
        return link.codigo
    finally:
        db.close()


# ─── Init ─────────────────────────────────────────────────────────────────────

def init_db():
    Base.metadata.create_all(bind=engine)
    _garantir_admin()


def _garantir_admin():
    db = get_db()
    try:
        existe = db.query(Usuario).filter_by(role="admin").first()
    finally:
        db.close()

    if not existe:
        criar_usuario("Admin", "admin", "admin", role="admin")
        print("[db] Admin criado — usuario: admin | senha: admin (troque depois!)")


if __name__ == "__main__":
    init_db()
    print("Database inicializada")