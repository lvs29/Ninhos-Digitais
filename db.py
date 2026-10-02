import os
import hashlib
import secrets
from datetime import date, datetime
from sqlalchemy import create_engine, text, Column, Integer, String, Text, ForeignKey, DateTime, Boolean, CheckConstraint, case
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

engine = create_engine("sqlite:///database.db", echo=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    nome       = Column(String, nullable=False)
    username   = Column(String, nullable=False, unique=True)
    senha_hash = Column(String, nullable=False)
    role       = Column(String, nullable=False, default="user")
    criado_em  = Column(DateTime, nullable=False, default=datetime.utcnow)

class Especie(Base):
    __tablename__ = "especies"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    nome                = Column(String, nullable=False, unique=True)
    nome_cientifico     = Column(String)
    descricao           = Column(Text)
    criado_em           = Column(DateTime, nullable=False, default=datetime.utcnow)

    link = relationship(
        "Link",
        back_populates="especie",
        uselist=False
    )

class Link(Base):
    __tablename__ = "links"

    id                  = Column(Integer, primary_key=True, autoincrement=True)

    especie_id = Column(
        Integer,
        ForeignKey("especies.id"),
        nullable=False,
        unique=True
    )

    especie = relationship(
        "Especie",
        back_populates="link"
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_db() -> Session:
    return SessionLocal()

def hash(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()

# ─── Init ─────────────────────────────────────────────────────────────────────

def init_db():
    Base.metadata.create_all(bind=engine)
    _garantir_admin()

def _garantir_admin():
    db = get_db()
    try:
        existe = db.query(Usuario).filter_by(role="admin").first()
        if not existe:
            criar_usuario("Admin", "admin", "admin", role="admin")
            print("[db] Admin criado — usuario: admin | senha: admin")
    finally:
        db.close()

if __name__ == "__main__":
    init_db()
    print("Database inicializada")