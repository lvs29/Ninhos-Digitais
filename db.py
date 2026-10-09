import os
import hashlib
import hmac
import secrets
from datetime import datetime

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, ForeignKey,
    DateTime, Boolean, func,
)
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


# ─── Modelos ──────────────────────────────────────────────────────────────────

class Usuario(Base):
    """Todo usuário é administrador (não existem papéis diferentes)."""
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    username = Column(String, nullable=False, unique=True)
    senha_hash = Column(String, nullable=False)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        # Nunca expõe senha_hash
        return {
            "id": self.id,
            "nome": self.nome,
            "username": self.username,
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

    vinculo = relationship(
        "Vinculo", back_populates="especie", uselist=False,
        cascade="save-update, merge, delete",
    )

    def to_dict(self):
        tag = self.vinculo.tag if self.vinculo else None
        return {
            "id": self.id,
            "nome": self.nome,
            "nome_cientifico": self.nome_cientifico,
            "distribuicao_geografica": self.distribuicao_geografica,
            "ameacada": self.ameacada,
            "tag": {"id": tag.id, "nome": tag.nome, "codigo": tag.codigo} if tag else None,
            "criado_em": self.criado_em.isoformat(),
        }


class Tag(Base):
    """
    Uma tag física (NFC, QR code impresso, etc.).
    O `codigo` é gerado pelo programa e NUNCA muda (já está gravado/impresso).
    O `nome` é só um rótulo editável para identificar a tag (ex.: "Sala 3").
    """
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False, unique=True)
    codigo = Column(String, nullable=False, unique=True)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    vinculo = relationship(
        "Vinculo", back_populates="tag", uselist=False,
        cascade="save-update, merge, delete",
    )

    def to_dict(self):
        especie = self.vinculo.especie if self.vinculo else None
        return {
            "id": self.id,
            "nome": self.nome,
            "codigo": self.codigo,
            "especie": {"id": especie.id, "nome": especie.nome} if especie else None,
            "criado_em": self.criado_em.isoformat(),
        }


class Vinculo(Base):
    """Tabela 1:1 entre espécie e tag (cada uma só pode aparecer uma vez)."""
    __tablename__ = "vinculos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    especie_id = Column(Integer, ForeignKey("especies.id"), nullable=False, unique=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False, unique=True)

    especie = relationship("Especie", back_populates="vinculo")
    tag = relationship("Tag", back_populates="vinculo")


# ─── Helpers ──────────────────────────────────────────────────────────────────

MIN_SENHA = 8


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


_HASH_FALSO = hash_senha("senha-falsa-para-igualar-tempo")


def _e_duplicado(erro: IntegrityError) -> bool:
    """True só se o erro for de valor repetido (UNIQUE). Outros erros (NOT NULL, etc.)
    indicam bug ou banco desatualizado e NÃO devem virar 'já existe'."""
    msg = str(erro.orig).lower()
    return "unique" in msg or "duplicate key" in msg


def _limpar(valor, campo: str) -> str:
    """Valida e normaliza um campo de texto obrigatório."""
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"O campo '{campo}' é obrigatório.")
    return valor.strip()


# ─── Usuários ─────────────────────────────────────────────────────────────────

def criar_usuario(
    nome: str, username: str, senha: str, exigir_senha_forte: bool = True,
) -> dict:
    nome = _limpar(nome, "nome")
    username = _limpar(username, "username")
    senha = _limpar(senha, "senha")
    if exigir_senha_forte and len(senha) < MIN_SENHA:
        raise ValueError(f"A senha deve ter pelo menos {MIN_SENHA} caracteres.")

    db = get_db()
    try:
        usuario = Usuario(nome=nome, username=username, senha_hash=hash_senha(senha))
        db.add(usuario)
        db.commit()
        return usuario.to_dict()
    except IntegrityError as e:
        db.rollback()
        if not _e_duplicado(e):
            raise
        raise ValueError(f"O usuário '{username}' já existe.")
    finally:
        db.close()


def autenticar_usuario(username: str, senha: str) -> dict | None:
    """Retorna o usuário (dict) se as credenciais estiverem corretas, senão None."""
    db = get_db()
    try:
        usuario = db.query(Usuario).filter_by(username=username).first()
        if usuario is None:
            verificar_senha(senha, _HASH_FALSO)  # evita revelar se o usuário existe
            return None
        if verificar_senha(senha, usuario.senha_hash):
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


def listar_usuarios() -> list[dict]:
    db = get_db()
    try:
        return [u.to_dict() for u in db.query(Usuario).order_by(Usuario.username).all()]
    finally:
        db.close()


def remover_usuario(usuario_id: int) -> bool:
    """Remove o usuário. Não permite remover o último (ninguém mais conseguiria entrar)."""
    db = get_db()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            return False
        if db.query(Usuario).count() <= 1:
            raise ValueError("Não é possível remover o último administrador.")
        db.delete(usuario)
        db.commit()
        return True
    finally:
        db.close()


def alterar_senha(usuario_id: int, senha_atual: str, nova_senha: str) -> None:
    """Troca a senha, exigindo a senha atual. Levanta ValueError se algo estiver errado."""
    if not isinstance(nova_senha, str) or len(nova_senha) < MIN_SENHA:
        raise ValueError(f"A nova senha deve ter pelo menos {MIN_SENHA} caracteres.")
    db = get_db()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario or not verificar_senha(senha_atual, usuario.senha_hash):
            raise ValueError("Senha atual incorreta.")
        usuario.senha_hash = hash_senha(nova_senha)
        db.commit()
    finally:
        db.close()


# ─── Vínculo espécie <-> tag (1:1, editável dos dois lados) ───────────────────

def _vincular(db: Session, especie: Especie, tag: Tag, lado: str) -> None:
    """
    Liga `especie` a `tag`. `lado` diz quem está sendo editado:
      "especie": troca a tag desta espécie (a tag antiga fica livre);
      "tag":     troca a espécie desta tag (a espécie antiga fica livre).
    Nunca "rouba" o par de um terceiro: se o outro lado já estiver ocupado,
    levanta ValueError e pede para desvincular antes.
    """
    atual_esp = db.query(Vinculo).filter_by(especie_id=especie.id).first()
    atual_tag = db.query(Vinculo).filter_by(tag_id=tag.id).first()

    if atual_esp and atual_tag and atual_esp.id == atual_tag.id:
        return  # já estão vinculadas

    if lado == "especie":
        if atual_tag:
            raise ValueError(
                f"A tag '{tag.nome}' já está vinculada à espécie "
                f"'{atual_tag.especie.nome}'. Desvincule-a antes."
            )
        if atual_esp:
            db.delete(atual_esp)
            db.flush()  # libera a UNIQUE antes de inserir o novo vínculo
    else:
        if atual_esp:
            raise ValueError(
                f"A espécie '{especie.nome}' já está vinculada à tag "
                f"'{atual_esp.tag.nome}'. Desvincule-a antes."
            )
        if atual_tag:
            db.delete(atual_tag)
            db.flush()

    db.add(Vinculo(especie_id=especie.id, tag_id=tag.id))


def _definir_tag_da_especie(db: Session, especie: Especie, tag_id: int | None) -> None:
    if tag_id is None:
        atual = db.query(Vinculo).filter_by(especie_id=especie.id).first()
        if atual:
            db.delete(atual)
            db.flush()
        return
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise ValueError("Tag não encontrada.")
    _vincular(db, especie, tag, lado="especie")


def _definir_especie_da_tag(db: Session, tag: Tag, especie_id: int | None) -> None:
    if especie_id is None:
        atual = db.query(Vinculo).filter_by(tag_id=tag.id).first()
        if atual:
            db.delete(atual)
            db.flush()
        return
    especie = db.get(Especie, especie_id)
    if especie is None:
        raise ValueError("Espécie não encontrada.")
    _vincular(db, especie, tag, lado="tag")


# ─── Tags ─────────────────────────────────────────────────────────────────────

def _nome_de_tag_em_uso(db: Session, nome: str, ignorar_id: int | None = None) -> bool:
    q = db.query(Tag).filter(func.lower(Tag.nome) == nome.lower())
    if ignorar_id is not None:
        q = q.filter(Tag.id != ignorar_id)
    return q.first() is not None


def criar_tag(nome: str, especie_id: int | None = None) -> dict:
    """Cria a tag com um código gerado pelo programa (fixo para sempre)."""
    nome = _limpar(nome, "nome")

    db = get_db()
    try:
        if _nome_de_tag_em_uso(db, nome):
            raise ValueError(f"Já existe uma tag chamada '{nome}'.")

        for _ in range(5):  # colisão de código é praticamente impossível; só por garantia
            tag = Tag(nome=nome, codigo=_novo_codigo())
            db.add(tag)
            try:
                db.flush()
                break
            except IntegrityError:
                db.rollback()
        else:
            raise RuntimeError("Não foi possível gerar um código único para a tag.")

        if especie_id is not None:
            _definir_especie_da_tag(db, tag, especie_id)
        db.commit()
        return tag.to_dict()
    finally:
        db.close()


def obter_tag(tag_id: int) -> dict | None:
    db = get_db()
    try:
        tag = db.get(Tag, tag_id)
        return tag.to_dict() if tag else None
    finally:
        db.close()


def obter_tag_por_codigo(codigo: str) -> dict | None:
    db = get_db()
    try:
        tag = db.query(Tag).filter_by(codigo=codigo).first()
        return tag.to_dict() if tag else None
    finally:
        db.close()


def listar_tags() -> list[dict]:
    db = get_db()
    try:
        return [t.to_dict() for t in db.query(Tag).order_by(Tag.nome).all()]
    finally:
        db.close()


def atualizar_tag(tag_id: int, **campos) -> dict | None:
    """
    Campos aceitos: nome, especie_id (None desvincula).
    O código NÃO pode ser alterado. Retorna a tag atualizada ou None se não existir.
    """
    permitidos = {"nome", "especie_id"}
    invalidos = set(campos) - permitidos
    if invalidos:
        raise ValueError(f"Campos inválidos: {', '.join(sorted(invalidos))}")

    db = get_db()
    try:
        tag = db.get(Tag, tag_id)
        if not tag:
            return None
        if "nome" in campos:
            nome = _limpar(campos["nome"], "nome")
            if _nome_de_tag_em_uso(db, nome, ignorar_id=tag.id):
                raise ValueError(f"Já existe uma tag chamada '{nome}'.")
            tag.nome = nome
        if "especie_id" in campos:
            _definir_especie_da_tag(db, tag, campos["especie_id"])
        db.commit()
        return tag.to_dict()
    except IntegrityError as e:
        db.rollback()
        if not _e_duplicado(e):
            raise
        raise ValueError("Já existe uma tag com esse nome.")
    finally:
        db.close()


def remover_tag(tag_id: int) -> bool:
    """Remove a tag e o vínculo dela (a espécie continua existindo)."""
    db = get_db()
    try:
        tag = db.get(Tag, tag_id)
        if not tag:
            return False
        db.delete(tag)
        db.commit()
        return True
    finally:
        db.close()


# ─── Espécies ─────────────────────────────────────────────────────────────────

def criar_especie(
    nome: str,
    nome_cientifico: str,
    distribuicao_geografica: str,
    ameacada: bool = False,
    tag_id: int | None = None,
) -> dict:
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
        db.add(especie)
        db.flush()
        if tag_id is not None:
            _definir_tag_da_especie(db, especie, tag_id)
        db.commit()
        return especie.to_dict()
    except IntegrityError as e:
        db.rollback()
        if not _e_duplicado(e):
            raise
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
    """Espécie ligada à tag com esse código (None se a tag não existir ou estiver livre)."""
    db = get_db()
    try:
        tag = db.query(Tag).filter_by(codigo=codigo).first()
        if tag is None or tag.vinculo is None:
            return None
        return tag.vinculo.especie.to_dict()
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
    Campos aceitos: nome, nome_cientifico, distribuicao_geografica, ameacada,
    tag_id (None desvincula). Retorna a espécie atualizada ou None se não existir.
    """
    permitidos = {"nome", "nome_cientifico", "distribuicao_geografica", "ameacada", "tag_id"}
    invalidos = set(campos) - permitidos
    if invalidos:
        raise ValueError(f"Campos inválidos: {', '.join(sorted(invalidos))}")

    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        if not especie:
            return None
        for campo, valor in campos.items():
            if campo == "tag_id":
                continue
            valor = bool(valor) if campo == "ameacada" else _limpar(valor, campo)
            setattr(especie, campo, valor)
        if "tag_id" in campos:
            _definir_tag_da_especie(db, especie, campos["tag_id"])
        db.commit()
        return especie.to_dict()
    except IntegrityError as e:
        db.rollback()
        if not _e_duplicado(e):
            raise
        raise ValueError("Já existe uma espécie com esse nome.")
    finally:
        db.close()


def remover_especie(especie_id: int) -> bool:
    """Remove a espécie e o vínculo dela (a tag continua existindo, livre)."""
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


# ─── Init ─────────────────────────────────────────────────────────────────────

def _verificar_esquema():
    """Falha cedo, com instrução clara, se o arquivo do banco for de uma versão antiga."""
    insp = inspect(engine)
    tabelas = set(insp.get_table_names())
    antigo = "links" in tabelas or (
        "usuarios" in tabelas
        and "role" in {c["name"] for c in insp.get_columns("usuarios")}
    )
    if antigo:
        raise RuntimeError(
            "O banco de dados é de uma versão antiga (tem a tabela 'links' ou a coluna "
            "'role'). Apague o arquivo database.db e inicie o programa de novo para "
            "recriá-lo. Atenção: isso apaga os dados cadastrados."
        )


def init_db():
    _verificar_esquema()
    Base.metadata.create_all(bind=engine)
    _garantir_admin()


def _garantir_admin():
    db = get_db()
    try:
        existe = db.query(Usuario).first()
    finally:
        db.close()

    if not existe:
        criar_usuario("Admin", "admin", "admin", exigir_senha_forte=False)
        print("[db] Admin criado — usuario: admin | senha: admin (troque depois!)")


if __name__ == "__main__":
    init_db()
    print("Database inicializada")