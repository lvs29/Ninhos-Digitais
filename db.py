import os
import json
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, ForeignKey,
    DateTime, Boolean, func, inspect,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

import imagens as armazem

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///database.db")

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


# ─── Constantes ───────────────────────────────────────────────────────────────

# Categorias da Lista Vermelha da IUCN (código -> nome em português)
CATEGORIAS_AMEACA = {
    "EX": "Extinta",
    "EW": "Extinta na natureza",
    "CR": "Criticamente em perigo",
    "EN": "Em perigo",
    "VU": "Vulnerável",
    "NT": "Quase ameaçada",
    "LC": "Pouco preocupante",
    "DD": "Dados insuficientes",
    "NE": "Não avaliada",
}
# Pela IUCN, "ameaçada de extinção" = CR, EN ou VU
CATEGORIAS_AMEACADAS = ("CR", "EN", "VU")

# Campos opcionais da espécie (texto livre; vazio = não informado)
CAMPOS_OPCIONAIS = (
    "nome_ingles", "nome_espanhol", "tipo_alimentacao",
    "peso", "tamanho", "longevidade",
)
MAX_OPCIONAL = 200
MIN_SENHA = 8
MAX_SENHA = 256
MAX_NOME = 200            # nomes (espécie, tag, usuário) e campos curtos
MAX_USERNAME = 100
MAX_TEXTO_LONGO = 5000    # distribuição geográfica


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

    # Obrigatórios
    nome = Column(String, nullable=False, unique=True)              # Nome comum
    nome_cientifico = Column(String, nullable=False)                # Nome científico
    distribuicao_geografica = Column(Text, nullable=False)          # Distribuição geográfica
    categoria_ameaca = Column(String, nullable=False, default="NE") # Categoria IUCN

    # Controle
    visivel = Column(Boolean, nullable=False, default=True)         # Aparece na página pública?
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Opcionais
    nome_ingles = Column(String)
    nome_espanhol = Column(String)
    tipo_alimentacao = Column(String)
    peso = Column(String)
    tamanho = Column(String)
    longevidade = Column(String)

    # Cada espécie fica em no máximo uma tag (viveiro/aquário)
    vinculo = relationship(
        "Vinculo", back_populates="especie", uselist=False,
        cascade="save-update, merge, delete",
    )

    # Galeria de imagens (ordenada)
    imagens = relationship(
        "Imagem", back_populates="especie",
        order_by="Imagem.ordem, Imagem.id",
        cascade="save-update, merge, delete",
    )

    # Quiz vinculado à espécie (1 para 1)
    quiz = relationship(
        "QuizEspecie", back_populates="especie", uselist=False,
        cascade="save-update, merge, delete",
    )

    def to_dict(self):
        tag = self.vinculo.tag if self.vinculo else None
        d = {
            "id": self.id,
            "nome": self.nome,
            "nome_cientifico": self.nome_cientifico,
            "distribuicao_geografica": self.distribuicao_geografica,
            "categoria_ameaca": self.categoria_ameaca,
            "categoria_ameaca_nome": CATEGORIAS_AMEACA.get(self.categoria_ameaca, ""),
            "ameacada": self.categoria_ameaca in CATEGORIAS_AMEACADAS,
            "visivel": self.visivel,
            "tag": {"id": tag.id, "nome": tag.nome, "codigo": tag.codigo} if tag else None,
            "imagens": [
                {"id": i.id, "token": i.token, "legenda": i.legenda,
                 "largura": i.largura, "altura": i.altura, "ordem": i.ordem}
                for i in self.imagens
            ],
            "tem_quiz": bool(self.quiz),
            "quiz": self.quiz.to_dict() if self.quiz else None,
            "criado_em": self.criado_em.isoformat(),
        }
        for campo in CAMPOS_OPCIONAIS:
            d[campo] = getattr(self, campo)
        return d


class QuizEspecie(Base):
    """Quiz de uma espécie, com perguntas e até 5 alternativas (1 correta por pergunta)."""
    __tablename__ = "quiz_especies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    especie_id = Column(Integer, ForeignKey("especies.id"), nullable=False, unique=True, index=True)
    titulo = Column(String(200), nullable=False, default="")
    perguntas_json = Column(Text, nullable=False, default="[]")
    atualizado_em = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    especie = relationship("Especie", back_populates="quiz")
    respostas = relationship(
        "RespostaQuiz", back_populates="quiz",
        cascade="save-update, merge, delete, delete-orphan",
    )

    def to_dict(self):
        try:
            perguntas = json.loads(self.perguntas_json or "[]")
        except (json.JSONDecodeError, TypeError):
            perguntas = []
        if not isinstance(perguntas, list):
            perguntas = []
        return {
            "id": self.id,
            "especie_id": self.especie_id,
            "titulo": self.titulo or "",
            "perguntas": perguntas,
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
        }


class RespostaQuiz(Base):
    """Tentativa anônima de um visitante no quiz de uma espécie."""
    __tablename__ = "quiz_respostas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    quiz_id = Column(Integer, ForeignKey("quiz_especies.id"), nullable=False, index=True)
    respostas_json = Column(Text, nullable=False, default="[]")
    acertos = Column(Integer, nullable=False, default=0)
    total_perguntas = Column(Integer, nullable=False, default=0)
    tempo_total_ms = Column(Integer, nullable=False, default=0)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    quiz = relationship("QuizEspecie", back_populates="respostas")


class Tag(Base):
    """
    Uma tag física (NFC, QR code impresso, etc.) responsável por um viveiro/aquário.
    O `codigo` é gerado pelo programa e NUNCA muda (já está gravado/impresso).
    O `nome` é só um rótulo editável para identificar a tag (ex.: "Sala 3").
    Uma tag comporta várias espécies.
    """
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False, unique=True)
    codigo = Column(String, nullable=False, unique=True)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    vinculos = relationship(
        "Vinculo", back_populates="tag",
        cascade="save-update, merge, delete",
    )
    acessos = relationship("Acesso", cascade="save-update, merge, delete")

    def to_dict(self):
        especies = sorted((v.especie for v in self.vinculos), key=lambda e: e.nome.lower())
        return {
            "id": self.id,
            "nome": self.nome,
            "codigo": self.codigo,
            "especies": [{"id": e.id, "nome": e.nome} for e in especies],
            "criado_em": self.criado_em.isoformat(),
        }


class Vinculo(Base):
    """
    Associação espécie -> tag.
    especie_id é UNIQUE (uma espécie só fica em uma tag); tag_id NÃO é
    (uma tag comporta várias espécies).
    """
    __tablename__ = "vinculos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    especie_id = Column(Integer, ForeignKey("especies.id"), nullable=False, unique=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False)

    especie = relationship("Especie", back_populates="vinculo")
    tag = relationship("Tag", back_populates="vinculos")


class Imagem(Base):
    """
    Uma imagem da galeria de uma espécie. Os arquivos ficam em disco (ver imagens.py)
    com o nome aleatório `token`; aqui só ficam os metadados.
    """
    __tablename__ = "imagens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    especie_id = Column(Integer, ForeignKey("especies.id"), nullable=False, index=True)
    token = Column(String(32), nullable=False, unique=True)
    largura = Column(Integer, nullable=False)   # da versão grande
    altura = Column(Integer, nullable=False)
    ordem = Column(Integer, nullable=False, default=0)
    legenda = Column(String)                    # também usada como texto alternativo
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)

    especie = relationship("Especie", back_populates="imagens")


class Acesso(Base):
    """Um acesso à página pública de uma tag (não guarda IP nem dados do visitante)."""
    __tablename__ = "acessos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), nullable=False, index=True)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


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


_HASH_FALSO = hash_senha("senha-falsa-para-igualar-tempo")


def _e_duplicado(erro: IntegrityError) -> bool:
    """True só se o erro for de valor repetido (UNIQUE). Outros erros (NOT NULL, etc.)
    indicam bug ou banco desatualizado e NÃO devem virar 'já existe'."""
    msg = str(erro.orig).lower()
    return "unique" in msg or "duplicate key" in msg


def _limpar(valor, campo: str, max_len: int = MAX_NOME) -> str:
    """Valida e normaliza um campo de texto obrigatório (com limite de tamanho)."""
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"O campo '{campo}' é obrigatório.")
    valor = valor.strip()
    if len(valor) > max_len:
        raise ValueError(f"O campo '{campo}' deve ter no máximo {max_len} caracteres.")
    return valor


def _escapar_like(texto: str) -> str:
    """Escapa % e _ para a busca tratá-los como texto comum."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _opcional(valor, campo: str) -> str | None:
    """Campo de texto opcional: vazio vira None; limita o tamanho."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValueError(f"O campo '{campo}' é inválido.")
    valor = valor.strip()
    if not valor:
        return None
    if len(valor) > MAX_OPCIONAL:
        raise ValueError(f"O campo '{campo}' deve ter no máximo {MAX_OPCIONAL} caracteres.")
    return valor


def _categoria(valor) -> str:
    codigo = (valor or "").strip().upper() if isinstance(valor, str) else ""
    if codigo not in CATEGORIAS_AMEACA:
        raise ValueError("Categoria de ameaça inválida.")
    return codigo


# ─── Usuários ─────────────────────────────────────────────────────────────────

def criar_usuario(
    nome: str, username: str, senha: str, exigir_senha_forte: bool = True,
) -> dict:
    nome = _limpar(nome, "nome")
    username = _limpar(username, "username", MAX_USERNAME)
    if not isinstance(senha, str) or not senha.strip():
        raise ValueError("O campo 'senha' é obrigatório.")
    if len(senha) > MAX_SENHA:
        raise ValueError(f"A senha deve ter no máximo {MAX_SENHA} caracteres.")
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
    if (not isinstance(username, str) or not isinstance(senha, str)
            or len(username) > 200 or len(senha) > 1024):
        return None  # entrada absurda: nem chega a consultar/derivar hash

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


class SenhaAtualIncorreta(ValueError):
    """A senha atual informada não confere (usada para limitar tentativas)."""


def alterar_senha(usuario_id: int, senha_atual: str, nova_senha: str) -> None:
    """Troca a senha, exigindo a senha atual. Levanta ValueError se algo estiver errado."""
    if not isinstance(nova_senha, str) or len(nova_senha) < MIN_SENHA:
        raise ValueError(f"A nova senha deve ter pelo menos {MIN_SENHA} caracteres.")
    if len(nova_senha) > MAX_SENHA:
        raise ValueError(f"A nova senha deve ter no máximo {MAX_SENHA} caracteres.")
    if not isinstance(senha_atual, str) or len(senha_atual) > 1024:
        raise SenhaAtualIncorreta("Senha atual incorreta.")
    db = get_db()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario or not verificar_senha(senha_atual, usuario.senha_hash):
            raise SenhaAtualIncorreta("Senha atual incorreta.")
        usuario.senha_hash = hash_senha(nova_senha)
        db.commit()
    finally:
        db.close()


# ─── Vínculo espécie -> tag (editável dos dois lados) ─────────────────────────

def _definir_tag_da_especie(db: Session, especie: Especie, tag_id: int | None) -> None:
    """Lado da espécie: troca (ou remove, com None) a tag desta espécie."""
    atual = db.query(Vinculo).filter_by(especie_id=especie.id).first()

    if tag_id is None:
        if atual:
            db.delete(atual)
            db.flush()
        return

    tag = db.get(Tag, tag_id)
    if tag is None:
        raise ValueError("Tag não encontrada.")
    if atual:
        if atual.tag_id == tag.id:
            return
        db.delete(atual)
        db.flush()  # libera a UNIQUE de especie_id antes de inserir o novo vínculo
    db.add(Vinculo(especie_id=especie.id, tag_id=tag.id))


def _definir_especies_da_tag(db: Session, tag: Tag, especie_ids) -> None:
    """
    Lado da tag: define exatamente quais espécies ficam nesta tag.
    Espécies que não estão na lista saem da tag. Nunca "rouba" uma espécie que
    já está em OUTRA tag: nesse caso levanta ValueError e pede para removê-la de lá.
    """
    novos = set(especie_ids or [])
    atuais = {v.especie_id: v for v in db.query(Vinculo).filter_by(tag_id=tag.id).all()}

    for eid in novos - set(atuais):
        especie = db.get(Especie, eid)
        if especie is None:
            raise ValueError("Espécie não encontrada.")
        outro = db.query(Vinculo).filter_by(especie_id=eid).first()
        if outro:
            raise ValueError(
                f"A espécie '{especie.nome}' já está na tag '{outro.tag.nome}'. "
                "Remova-a de lá antes."
            )

    for eid, vinculo in atuais.items():
        if eid not in novos:
            db.delete(vinculo)
    db.flush()

    for eid in novos - set(atuais):
        db.add(Vinculo(especie_id=eid, tag_id=tag.id))


# ─── Tags ─────────────────────────────────────────────────────────────────────

def _nome_de_tag_em_uso(db: Session, nome: str, ignorar_id: int | None = None) -> bool:
    q = db.query(Tag).filter(func.lower(Tag.nome) == nome.lower())
    if ignorar_id is not None:
        q = q.filter(Tag.id != ignorar_id)
    return q.first() is not None


def criar_tag(nome: str, especie_ids: list[int] | None = None) -> dict:
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

        if especie_ids:
            _definir_especies_da_tag(db, tag, especie_ids)
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
    """Lista as tags (com espécies e estatísticas de acesso) em ordem alfabética."""
    db = get_db()
    try:
        tags = [t.to_dict() for t in db.query(Tag).order_by(Tag.nome).all()]
    finally:
        db.close()

    stats = estatisticas_acessos()
    vazio = {"total": 0, "ultimos_7_dias": 0, "ultimo": None}
    for t in tags:
        t["acessos"] = stats.get(t["id"], vazio)
    return tags


def atualizar_tag(tag_id: int, **campos) -> dict | None:
    """
    Campos aceitos: nome, especie_ids (lista; define exatamente as espécies da tag).
    O código NÃO pode ser alterado. Retorna a tag atualizada ou None se não existir.
    """
    permitidos = {"nome", "especie_ids"}
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
        if "especie_ids" in campos:
            _definir_especies_da_tag(db, tag, campos["especie_ids"])
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
    """Remove a tag, os vínculos e o histórico de acessos (as espécies continuam existindo)."""
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
    categoria_ameaca: str = "NE",
    visivel: bool = True,
    tag_id: int | None = None,
    imagens: list | None = None,
    **opcionais,
) -> dict:
    """
    Campos opcionais aceitos em **opcionais: nome_ingles, nome_espanhol,
    tipo_alimentacao, peso, tamanho, longevidade.
    imagens: lista de imagens já processadas (imagens.Processada) para a galeria.
    A espécie e as imagens são gravadas juntas; se algo falhar, nada fica para trás.
    """
    invalidos = set(opcionais) - set(CAMPOS_OPCIONAIS)
    if invalidos:
        raise ValueError(f"Campos inválidos: {', '.join(sorted(invalidos))}")

    nome = _limpar(nome, "nome")
    nome_cientifico = _limpar(nome_cientifico, "nome_cientifico")
    distribuicao_geografica = _limpar(distribuicao_geografica, "distribuicao_geografica", MAX_TEXTO_LONGO)
    categoria_ameaca = _categoria(categoria_ameaca)
    extras = {c: _opcional(opcionais.get(c), c) for c in CAMPOS_OPCIONAIS}
    novas = list(imagens or [])
    if len(novas) > armazem.MAX_POR_ESPECIE:
        raise ValueError(f"Cada espécie pode ter no máximo {armazem.MAX_POR_ESPECIE} imagens.")

    gravados: list[str] = []  # arquivos já escritos (para desfazer se algo falhar)
    db = get_db()
    try:
        especie = Especie(
            nome=nome,
            nome_cientifico=nome_cientifico,
            distribuicao_geografica=distribuicao_geografica,
            categoria_ameaca=categoria_ameaca,
            visivel=bool(visivel),
            **extras,
        )
        db.add(especie)
        db.flush()
        if tag_id is not None:
            _definir_tag_da_especie(db, especie, tag_id)
        for ordem, proc in enumerate(novas):
            token = armazem.salvar(proc)
            gravados.append(token)
            db.add(Imagem(especie_id=especie.id, token=token,
                          largura=proc.largura, altura=proc.altura, ordem=ordem))
        db.commit()
        gravados = []  # gravado com sucesso: nada a desfazer
        return especie.to_dict()
    except IntegrityError as e:
        db.rollback()
        armazem.apagar(gravados)
        if not _e_duplicado(e):
            raise
        raise ValueError(f"A espécie '{nome}' já está cadastrada.")
    except Exception:
        armazem.apagar(gravados)
        raise
    finally:
        db.close()


def obter_especie(especie_id: int) -> dict | None:
    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        return especie.to_dict() if especie else None
    finally:
        db.close()


def listar_especies_por_codigo(codigo: str, apenas_visiveis: bool = True) -> list[dict]:
    """Espécies da tag com esse código (usada pela página pública)."""
    db = get_db()
    try:
        tag = db.query(Tag).filter_by(codigo=codigo).first()
        if tag is None:
            return []
        especies = [v.especie for v in tag.vinculos]
        if apenas_visiveis:
            especies = [e for e in especies if e.visivel]
        especies.sort(key=lambda e: e.nome.lower())
        return [e.to_dict() for e in especies]
    finally:
        db.close()


def listar_especies(
    busca: str | None = None,
    ameacada: bool | None = None,
    categoria: str | None = None,
    visivel: bool | None = None,
) -> list[dict]:
    """
    Lista espécies em ordem alfabética.
      busca:     nome comum, científico, inglês ou espanhol (contém, sem diferenciar maiúsculas)
      ameacada:  True = só CR/EN/VU; False = todas as outras
      categoria: código IUCN exato (ex.: "EN")
      visivel:   True/False filtra pela visibilidade pública
    """
    db = get_db()
    try:
        q = db.query(Especie)
        if busca and busca.strip():
            padrao = f"%{_escapar_like(busca.strip()[:100])}%"
            q = q.filter(
                Especie.nome.ilike(padrao, escape="\\")
                | Especie.nome_cientifico.ilike(padrao, escape="\\")
                | Especie.nome_ingles.ilike(padrao, escape="\\")
                | Especie.nome_espanhol.ilike(padrao, escape="\\")
            )
        if ameacada is True:
            q = q.filter(Especie.categoria_ameaca.in_(CATEGORIAS_AMEACADAS))
        elif ameacada is False:
            q = q.filter(~Especie.categoria_ameaca.in_(CATEGORIAS_AMEACADAS))
        if categoria:
            q = q.filter(Especie.categoria_ameaca == _categoria(categoria))
        if visivel is not None:
            q = q.filter(Especie.visivel == visivel)
        return [e.to_dict() for e in q.order_by(Especie.nome).all()]
    finally:
        db.close()


def _ordem(valor) -> int:
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise ValueError("A ordem da imagem deve ser um número.")
    if not 0 <= n <= 999:
        raise ValueError("A ordem da imagem deve estar entre 0 e 999.")
    return n


def _legenda(valor) -> str | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ValueError("Legenda inválida.")
    valor = valor.strip()
    if len(valor) > armazem.MAX_LEGENDA:
        raise ValueError(f"A legenda deve ter no máximo {armazem.MAX_LEGENDA} caracteres.")
    return valor or None


def _atualizar_imagens(db: Session, especie: Especie, campos: dict, gravados: list[str]) -> list[str]:
    """
    Aplica remoções, ajustes (ordem/legenda) e novas imagens de uma espécie.
    Registra em `gravados` os arquivos escritos (para o chamador desfazer em caso de erro)
    e devolve os tokens removidos (cujos arquivos devem ser apagados após o commit).
    Ids de imagens que não pertencem à espécie são ignorados.
    """
    novas = list(campos.get("imagens_novas") or [])
    remover = set(campos.get("imagens_remover") or [])
    ajustes = campos.get("imagens_ajustes") or {}
    if not (novas or remover or ajustes):
        return []

    existentes = {i.id: i for i in db.query(Imagem).filter_by(especie_id=especie.id).all()}
    a_remover = {i: existentes[i] for i in remover if i in existentes}
    restantes = [im for i, im in existentes.items() if i not in a_remover]
    if len(restantes) + len(novas) > armazem.MAX_POR_ESPECIE:
        raise ValueError(
            f"Cada espécie pode ter no máximo {armazem.MAX_POR_ESPECIE} imagens "
            f"({len(restantes)} já cadastradas)."
        )

    # Valida/aplica ajustes ANTES de gravar qualquer arquivo
    for id_, ajuste in ajustes.items():
        imagem = existentes.get(id_)
        if imagem is None or id_ in a_remover:
            continue
        if "ordem" in ajuste:
            imagem.ordem = _ordem(ajuste["ordem"])
        if "legenda" in ajuste:
            imagem.legenda = _legenda(ajuste["legenda"])

    tokens_removidos = []
    for imagem in a_remover.values():
        tokens_removidos.append(imagem.token)
        db.delete(imagem)

    proxima = max([im.ordem for im in restantes] or [-1]) + 1
    for k, proc in enumerate(novas):
        token = armazem.salvar(proc)
        gravados.append(token)
        db.add(Imagem(especie_id=especie.id, token=token,
                      largura=proc.largura, altura=proc.altura, ordem=proxima + k))
    return tokens_removidos


def atualizar_especie(especie_id: int, **campos) -> dict | None:
    """
    Campos aceitos: nome, nome_cientifico, distribuicao_geografica, categoria_ameaca,
    visivel, tag_id (None remove da tag) e os opcionais (nome_ingles, nome_espanhol,
    tipo_alimentacao, peso, tamanho, longevidade; vazio limpa o campo).

    Galeria (tudo na mesma transação):
      imagens_novas:   lista de imagens processadas (imagens.Processada)
      imagens_remover: ids de imagens a remover
      imagens_ajustes: {id: {"ordem": n, "legenda": "texto"}}

    Retorna a espécie atualizada ou None se não existir.
    """
    permitidos = (
        {"nome", "nome_cientifico", "distribuicao_geografica",
         "categoria_ameaca", "visivel", "tag_id",
         "imagens_novas", "imagens_remover", "imagens_ajustes"} | set(CAMPOS_OPCIONAIS)
    )
    invalidos = set(campos) - permitidos
    if invalidos:
        raise ValueError(f"Campos inválidos: {', '.join(sorted(invalidos))}")

    gravados: list[str] = []
    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        if not especie:
            return None
        for campo, valor in campos.items():
            if campo == "tag_id" or campo.startswith("imagens_"):
                continue
            if campo == "visivel":
                valor = bool(valor)
            elif campo == "categoria_ameaca":
                valor = _categoria(valor)
            elif campo in CAMPOS_OPCIONAIS:
                valor = _opcional(valor, campo)
            elif campo == "distribuicao_geografica":
                valor = _limpar(valor, campo, MAX_TEXTO_LONGO)
            else:
                valor = _limpar(valor, campo)
            setattr(especie, campo, valor)
        if "tag_id" in campos:
            _definir_tag_da_especie(db, especie, campos["tag_id"])
        removidos = _atualizar_imagens(db, especie, campos, gravados)
        db.commit()
        gravados = []
        armazem.apagar(removidos)  # só depois do commit
        return especie.to_dict()
    except IntegrityError as e:
        db.rollback()
        armazem.apagar(gravados)
        if not _e_duplicado(e):
            raise
        raise ValueError("Já existe uma espécie com esse nome.")
    except Exception:
        armazem.apagar(gravados)
        raise
    finally:
        db.close()


def remover_especie(especie_id: int) -> bool:
    """Remove a espécie, o vínculo e as imagens dela (a tag continua existindo)."""
    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        if not especie:
            return False
        tokens = [im.token for im in especie.imagens]
        db.delete(especie)
        db.commit()
        armazem.apagar(tokens)  # só depois do commit
        return True
    finally:
        db.close()


def imagem_visivel(token: str) -> bool | None:
    """
    Usada ao servir imagens: True/False conforme a espécie dona da imagem esteja
    visível ao público, ou None se não existir imagem com esse token.
    """
    db = get_db()
    try:
        linha = (
            db.query(Especie.visivel).select_from(Imagem)
            .join(Especie, Especie.id == Imagem.especie_id)
            .filter(Imagem.token == token).first()
        )
        return None if linha is None else bool(linha[0])
    finally:
        db.close()


# ─── Quiz por espécie ─────────────────────────────────────────────────────────

def obter_quiz_especie(especie_id: int) -> dict | None:
    db = get_db()
    try:
        quiz = db.query(QuizEspecie).filter_by(especie_id=especie_id).first()
        return quiz.to_dict() if quiz else None
    finally:
        db.close()


def salvar_quiz_especie(especie_id: int, quiz_data: dict) -> dict:
    titulo = (quiz_data.get("titulo") or "").strip()[:MAX_NOME]
    perguntas = quiz_data.get("perguntas") or []
    perguntas_json = json.dumps(perguntas, ensure_ascii=False)

    db = get_db()
    try:
        especie = db.get(Especie, especie_id)
        if especie is None:
            raise ValueError("Espécie não encontrada.")

        quiz = db.query(QuizEspecie).filter_by(especie_id=especie_id).first()
        if quiz is None:
            quiz = QuizEspecie(
                especie_id=especie_id,
                titulo=titulo,
                perguntas_json=perguntas_json,
            )
            db.add(quiz)
        else:
            quiz.titulo = titulo
            quiz.perguntas_json = perguntas_json
            quiz.atualizado_em = datetime.utcnow()

        db.commit()
        db.refresh(quiz)
        return quiz.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


MAX_PERGUNTAS_QUIZ_PUBLICO = 3


def registrar_resposta_quiz(
    especie_id: int,
    respostas: list[dict],
    tempo_total_ms: int,
) -> dict:
    """
    Salva uma tentativa de quiz.
    `respostas`: lista no formato
    [{"pergunta_idx": int, "alternativa_idx": int, "tempo_ms": int (opcional)}, ...]
    """
    db = get_db()
    try:
        quiz = db.query(QuizEspecie).filter_by(especie_id=especie_id).first()
        if quiz is None:
            raise ValueError("Quiz não encontrado para esta espécie.")

        perguntas = quiz.to_dict()["perguntas"]
        if not perguntas:
            raise ValueError("Este quiz não possui perguntas.")

        qtd_esperada = min(len(perguntas), MAX_PERGUNTAS_QUIZ_PUBLICO)
        if not isinstance(respostas, list) or len(respostas) != qtd_esperada:
            raise ValueError("Quantidade de respostas incompatível com o quiz.")

        acertos = 0
        detalhes = []
        perguntas_respondidas = set()

        for idx_item, item in enumerate(respostas):
            if isinstance(item, int) and not isinstance(item, bool):
                p_idx = idx_item
                alt_idx = item
                tempo_p = None
            elif isinstance(item, dict):
                p_idx = item.get("pergunta_idx", idx_item)
                alt_idx = item.get("alternativa_idx")
                tempo_p = item.get("tempo_ms")
            else:
                raise ValueError("Formato de resposta inválido.")

            if (
                not isinstance(p_idx, int)
                or isinstance(p_idx, bool)
                or not (0 <= p_idx < len(perguntas))
                or p_idx in perguntas_respondidas
            ):
                raise ValueError("Pergunta inválida nas respostas enviadas.")

            perguntas_respondidas.add(p_idx)
            pergunta = perguntas[p_idx]
            alts = pergunta.get("alternativas", [])

            if (
                not isinstance(alt_idx, int)
                or isinstance(alt_idx, bool)
                or not (0 <= alt_idx < len(alts))
            ):
                raise ValueError(f"Alternativa inválida na pergunta {idx_item + 1}.")

            correta = bool(alts[alt_idx].get("correta"))
            if correta:
                acertos += 1

            registro_item = {
                "pergunta_idx": p_idx,
                "alternativa_idx": alt_idx,
                "correta": correta,
            }
            if (
                isinstance(tempo_p, (int, float))
                and not isinstance(tempo_p, bool)
                and 0 <= tempo_p <= 3_600_000
            ):
                registro_item["tempo_ms"] = int(tempo_p)
            detalhes.append(registro_item)

        if isinstance(tempo_total_ms, bool) or not isinstance(tempo_total_ms, (int, float)):
            tempo_ms = 0
        else:
            tempo_ms = max(0, min(int(tempo_total_ms), 3_600_000))

        tentativa = RespostaQuiz(
            quiz_id=quiz.id,
            respostas_json=json.dumps(detalhes, ensure_ascii=False),
            acertos=acertos,
            total_perguntas=len(detalhes),
            tempo_total_ms=tempo_ms,
        )
        db.add(tentativa)
        db.commit()
        return {
            "acertos": acertos,
            "total_perguntas": len(detalhes),
            "tempo_total_ms": tempo_ms,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def estatisticas_quiz_especie(especie_id: int) -> dict | None:
    """Agrega alternativas mais marcadas, taxa de acerto e tempo médio de resposta."""
    db = get_db()
    try:
        quiz = db.query(QuizEspecie).filter_by(especie_id=especie_id).first()
        if quiz is None:
            return None

        perguntas = quiz.to_dict()["perguntas"]
        tentativas = (
            db.query(RespostaQuiz)
            .filter_by(quiz_id=quiz.id)
            .order_by(RespostaQuiz.criado_em.desc())
            .all()
        )

        contagem = [
            [0] * len(p.get("alternativas", []))
            for p in perguntas
        ]
        tempos_pergunta: list[list[int]] = [[] for _ in perguntas]
        tempos_totais: list[int] = []
        soma_acertos = 0
        soma_questoes = 0

        for t in tentativas:
            if t.tempo_total_ms and t.tempo_total_ms > 0:
                tempos_totais.append(t.tempo_total_ms)
            soma_acertos += t.acertos or 0
            soma_questoes += t.total_perguntas or 0

            try:
                itens = json.loads(t.respostas_json or "[]")
            except (json.JSONDecodeError, TypeError):
                continue

            for idx_item, item in enumerate(itens):
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("pergunta_idx"), int)
                    and not isinstance(item.get("pergunta_idx"), bool)
                ):
                    p_idx = item["pergunta_idx"]
                else:
                    p_idx = idx_item

                if not (0 <= p_idx < len(perguntas)):
                    continue

                alt_idx = item if (isinstance(item, int) and not isinstance(item, bool)) else (
                    item.get("alternativa_idx") if isinstance(item, dict) else None
                )
                if (
                    isinstance(alt_idx, int)
                    and not isinstance(alt_idx, bool)
                    and 0 <= alt_idx < len(contagem[p_idx])
                ):
                    contagem[p_idx][alt_idx] += 1
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("tempo_ms"), int)
                    and not isinstance(item.get("tempo_ms"), bool)
                    and item["tempo_ms"] > 0
                ):
                    tempos_pergunta[p_idx].append(item["tempo_ms"])

        perguntas_stats = []
        for p_idx, p in enumerate(perguntas):
            votos_alts = contagem[p_idx]
            total_votos = sum(votos_alts)
            max_votos = max(votos_alts) if total_votos > 0 else 0
            votos_corretos = 0

            alts_stats = []
            for a_idx, alt in enumerate(p.get("alternativas", [])):
                votos = votos_alts[a_idx]
                if alt.get("correta"):
                    votos_corretos += votos
                pct = round((votos / total_votos) * 100, 1) if total_votos > 0 else 0.0
                alts_stats.append({
                    "texto": alt.get("texto", ""),
                    "correta": bool(alt.get("correta")),
                    "votos": votos,
                    "percentual": pct,
                    "mais_marcada": votos > 0 and votos == max_votos,
                })

            tp = tempos_pergunta[p_idx]
            perguntas_stats.append({
                "enunciado": p.get("enunciado", ""),
                "total_votos": total_votos,
                "taxa_acerto": round((votos_corretos / total_votos) * 100, 1) if total_votos > 0 else 0.0,
                "tempo_medio_s": round((sum(tp) / len(tp)) / 1000, 1) if tp else None,
                "alternativas": alts_stats,
            })

        total_por_tentativa = min(len(perguntas), MAX_PERGUNTAS_QUIZ_PUBLICO)
        return {
            "total_respostas": len(tentativas),
            "taxa_acerto_geral": round((soma_acertos / soma_questoes) * 100, 1) if soma_questoes > 0 else 0.0,
            "media_acertos": round(soma_acertos / len(tentativas), 1) if tentativas else 0.0,
            "total_perguntas": total_por_tentativa,
            "tempo_medio_s": round((sum(tempos_totais) / len(tempos_totais)) / 1000, 1) if tempos_totais else 0.0,
            "tempo_min_s": round(min(tempos_totais) / 1000, 1) if tempos_totais else 0.0,
            "tempo_max_s": round(max(tempos_totais) / 1000, 1) if tempos_totais else 0.0,
            "ultima_resposta": tentativas[0].criado_em.isoformat() if tentativas else None,
            "perguntas": perguntas_stats,
        }
    finally:
        db.close()


def limpar_respostas_quiz(especie_id: int) -> int:
    """Remove o histórico de respostas do quiz de uma espécie (útil após editar perguntas)."""
    db = get_db()
    try:
        quiz = db.query(QuizEspecie).filter_by(especie_id=especie_id).first()
        if quiz is None:
            return 0
        removidas = db.query(RespostaQuiz).filter_by(quiz_id=quiz.id).delete()
        db.commit()
        return removidas
    finally:
        db.close()


# ─── Acessos ──────────────────────────────────────────────────────────────────

def registrar_acesso(tag_id: int) -> None:
    db = get_db()
    try:
        db.add(Acesso(tag_id=tag_id))
        db.commit()
    finally:
        db.close()


def estatisticas_acessos() -> dict[int, dict]:
    """
    Retorna {tag_id: {"total": n, "ultimos_7_dias": n, "ultimo": "ISO UTC"}}.
    Tags sem nenhum acesso não aparecem no resultado.
    """
    db = get_db()
    try:
        limite = datetime.utcnow() - timedelta(days=7)
        stats: dict[int, dict] = {}

        total = db.query(
            Acesso.tag_id, func.count(Acesso.id), func.max(Acesso.criado_em)
        ).group_by(Acesso.tag_id)
        for tag_id, n, ultimo in total:
            stats[tag_id] = {"total": n, "ultimos_7_dias": 0, "ultimo": ultimo.isoformat()}

        recentes = db.query(Acesso.tag_id, func.count(Acesso.id)) \
            .filter(Acesso.criado_em >= limite).group_by(Acesso.tag_id)
        for tag_id, n in recentes:
            stats[tag_id]["ultimos_7_dias"] = n
        return stats
    finally:
        db.close()


# ─── Init ─────────────────────────────────────────────────────────────────────

def _verificar_esquema():
    """Falha cedo, com instrução clara, se o arquivo do banco for de uma versão antiga."""
    insp = inspect(engine)
    tabelas = set(insp.get_table_names())

    antigo = "links" in tabelas
    if "usuarios" in tabelas:
        antigo = antigo or "role" in {c["name"] for c in insp.get_columns("usuarios")}
    if "especies" in tabelas:
        antigo = antigo or "ameacada" in {c["name"] for c in insp.get_columns("especies")}

    if antigo:
        raise RuntimeError(
            "O banco de dados é de uma versão antiga (esquema desatualizado). "
            "Apague o arquivo database.db e inicie o programa de novo para recriá-lo. "
            "Atenção: isso apaga os dados cadastrados."
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