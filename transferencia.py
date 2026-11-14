"""
Exportação e importação dos dados em JSON (um arquivo por tipo de dado).

Formato de cada arquivo:
    {
      "formato": "ninhos-digitais",
      "tipo": "especies",            # especies | tags | vinculos | usuarios | acessos
      "versao": 1,
      "exportado_em": "2026-10-31T12:00:00Z",
      "itens": [ ... ]
    }

Os arquivos não têm IDs internos do banco: usam chaves naturais
(nome da espécie, código da tag, login do usuário).

A importação MESCLA: cria o que é novo e atualiza o que já existe, nunca apaga.
Primeiro tudo é validado sem gravar nada; um arquivo com qualquer erro é rejeitado
inteiro (e os demais arquivos válidos do mesmo envio continuam).
"""
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone

from sqlalchemy import insert

import db
from db import (
    Acesso, Especie, Tag, Usuario, Vinculo,
    CAMPOS_OPCIONAIS, CATEGORIAS_AMEACA, MAX_OPCIONAL,
)

log = logging.getLogger(__name__)

FORMATO = "ninhos-digitais"
VERSAO = 1
MAX_ERROS = 50  # erros detalhados mostrados por arquivo
MAX_ITENS = 200_000  # itens por arquivo importado

# A ordem aqui é a ordem de importação (quem é referenciado vem primeiro).
TIPOS = {
    "especies": {
        "rotulo": "Espécies",
        "icone": "bi-tree",
        "descricao": "Nomes, categoria de ameaça, visibilidade e informações opcionais.",
        "sensivel": False,
    },
    "tags": {
        "rotulo": "Tags",
        "icone": "bi-qr-code",
        "descricao": "Nome e código fixo de cada tag (QR code / NFC).",
        "sensivel": False,
    },
    "vinculos": {
        "rotulo": "Vínculos",
        "icone": "bi-link-45deg",
        "descricao": "Qual espécie está em qual tag (viveiro/aquário).",
        "sensivel": False,
    },
    "usuarios": {
        "rotulo": "Usuários",
        "icone": "bi-people",
        "descricao": "Administradores do sistema, com as senhas (em hash).",
        "sensivel": True,
    },
    "acessos": {
        "rotulo": "Acessos",
        "icone": "bi-graph-up",
        "descricao": "Histórico de visitas às páginas públicas (data e tag).",
        "sensivel": False,
    },
}

RE_CODIGO = re.compile(r"[A-Za-z0-9_-]{6,64}")               # mesmo alfabeto de token_urlsafe (use fullmatch)
RE_HASH = re.compile(r"[0-9a-f]{32}\$[0-9a-f]{64}")          # salt(16 bytes)$pbkdf2-sha256 (use fullmatch)


# ═════════════════════════════ Exportação ═════════════════════════════

def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_utc(dt: datetime) -> str:
    return dt.isoformat() + "Z"  # os horários do banco são UTC


def _itens(tipo: str, sess) -> list[dict]:
    if tipo == "especies":
        campos = ("nome", "nome_cientifico", "distribuicao_geografica",
                  "categoria_ameaca", "visivel") + CAMPOS_OPCIONAIS
        return [
            {c: getattr(e, c) for c in campos}
            for e in sess.query(Especie).order_by(Especie.nome).all()
        ]
    if tipo == "tags":
        return [
            {"codigo": t.codigo, "nome": t.nome}
            for t in sess.query(Tag).order_by(Tag.nome).all()
        ]
    if tipo == "vinculos":
        linhas = (
            sess.query(Especie.nome, Tag.codigo).select_from(Vinculo)
            .join(Especie, Especie.id == Vinculo.especie_id)
            .join(Tag, Tag.id == Vinculo.tag_id)
            .order_by(Especie.nome).all()
        )
        return [{"especie": nome, "tag": codigo} for nome, codigo in linhas]
    if tipo == "usuarios":
        return [
            {"username": u.username, "nome": u.nome, "senha_hash": u.senha_hash}
            for u in sess.query(Usuario).order_by(Usuario.username).all()
        ]
    if tipo == "acessos":
        linhas = (
            sess.query(Tag.codigo, Acesso.criado_em).select_from(Acesso)
            .join(Tag, Tag.id == Acesso.tag_id)
            .order_by(Acesso.criado_em, Acesso.id).all()
        )
        return [{"tag": codigo, "criado_em": _iso_utc(dt)} for codigo, dt in linhas]
    raise KeyError(tipo)


def exportar(tipo: str) -> dict:
    """Retorna o arquivo (já no formato final) de um tipo de dado."""
    sess = db.get_db()
    try:
        return {
            "formato": FORMATO,
            "tipo": tipo,
            "versao": VERSAO,
            "exportado_em": _agora(),
            "itens": _itens(tipo, sess),
        }
    finally:
        sess.close()


def serializar(envelope: dict) -> bytes:
    return json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8")


def nome_arquivo(tipo: str) -> str:
    return f"ninhos-digitais_{tipo}_{datetime.now().strftime('%Y-%m-%d')}.json"


def exportar_zip() -> tuple[bytes, str]:
    """Todos os tipos num único .zip. Retorna (bytes, nome do arquivo)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for tipo in TIPOS:
            z.writestr(f"{tipo}.json", serializar(exportar(tipo)))
    return buffer.getvalue(), f"ninhos-digitais_backup_{datetime.now().strftime('%Y-%m-%d')}.zip"


def contagens() -> dict[str, int]:
    """Quantos itens existem de cada tipo (para exibir na tela)."""
    sess = db.get_db()
    try:
        return {
            "especies": sess.query(Especie).count(),
            "tags": sess.query(Tag).count(),
            "vinculos": sess.query(Vinculo).count(),
            "usuarios": sess.query(Usuario).count(),
            "acessos": sess.query(Acesso).count(),
        }
    finally:
        sess.close()


# ═════════════════════════════ Validação ═════════════════════════════

class _Erros:
    """Acumula mensagens de erro (guardando só as primeiras)."""

    def __init__(self):
        self.mensagens: list[str] = []
        self.total = 0

    def add(self, msg: str) -> None:
        self.total += 1
        if len(self.mensagens) < MAX_ERROS:
            self.mensagens.append(msg)

    def __bool__(self) -> bool:
        return self.total > 0

    def lista(self) -> list[str]:
        resto = self.total - len(self.mensagens)
        return self.mensagens + ([f"... e mais {resto} erro(s)."] if resto > 0 else [])


def _chaves_ok(item, permitidas: set, obrigatorias: set, pref: str, erros: _Erros) -> bool:
    if not isinstance(item, dict):
        erros.add(f"{pref}: deveria ser um objeto JSON.")
        return False
    desconhecidas = set(item) - permitidas
    faltando = obrigatorias - set(item)
    if desconhecidas:
        erros.add(f"{pref}: campo(s) desconhecido(s): {', '.join(sorted(map(str, desconhecidas)))}.")
    if faltando:
        erros.add(f"{pref}: campo(s) obrigatório(s) ausente(s): {', '.join(sorted(faltando))}.")
    return not desconhecidas and not faltando


def _texto(item: dict, campo: str, max_len: int, pref: str, erros: _Erros) -> str | None:
    """Texto obrigatório e não vazio."""
    valor = item[campo]
    if not isinstance(valor, str) or not valor.strip():
        erros.add(f"{pref}: '{campo}' deve ser um texto não vazio.")
        return None
    valor = valor.strip()
    if len(valor) > max_len:
        erros.add(f"{pref}: '{campo}' passa de {max_len} caracteres.")
        return None
    return valor


def _ler_envelope(conteudo: bytes):
    """Retorna (tipo, itens, erros)."""
    erros = _Erros()
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        erros.add("O arquivo não está em UTF-8.")
        return None, None, erros
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError as e:
        erros.add(f"JSON inválido: {e.msg} (linha {e.lineno}, coluna {e.colno}).")
        return None, None, erros
    except RecursionError:
        erros.add("JSON com estrutura profunda demais.")
        return None, None, erros

    if not isinstance(dados, dict) or dados.get("formato") != FORMATO:
        erros.add("Este não é um arquivo exportado pelo Ninhos Digitais.")
        return None, None, erros
    tipo = dados.get("tipo")
    if tipo not in TIPOS:
        erros.add(f"Tipo de dado desconhecido: {tipo!r}.")
        return None, None, erros
    versao = dados.get("versao")
    if type(versao) is not int or versao != VERSAO:
        erros.add(f"Versão de formato não suportada: {versao!r} (esperado {VERSAO}).")
        return None, None, erros
    itens = dados.get("itens")
    if not isinstance(itens, list):
        erros.add("O campo 'itens' deve ser uma lista.")
        return None, None, erros
    if len(itens) > MAX_ITENS:
        erros.add(f"Itens demais no arquivo (máximo {MAX_ITENS}).")
        return None, None, erros
    return tipo, itens, erros


# ═════════════════════════════ Planejamento (só leitura) ═════════════════════════════
# Cada planejador valida os itens, compara com o banco e devolve (plano, contagem).
# Nada é gravado aqui.

def _planejar_especies(sess, itens, ctx, erros):
    existentes = {e.nome: e for e in sess.query(Especie).all()}
    permitidas = {"nome", "nome_cientifico", "distribuicao_geografica",
                  "categoria_ameaca", "visivel", *CAMPOS_OPCIONAIS}
    plano, vistos = [], set()
    cont = {"criados": 0, "atualizados": 0, "iguais": 0}

    for i, item in enumerate(itens, 1):
        pref = f"Item {i}"
        if not _chaves_ok(item, permitidas, {"nome", "nome_cientifico", "distribuicao_geografica"}, pref, erros):
            continue
        antes = erros.total
        campos = {
            "nome": _texto(item, "nome", 200, pref, erros),
            "nome_cientifico": _texto(item, "nome_cientifico", 200, pref, erros),
            "distribuicao_geografica": _texto(item, "distribuicao_geografica", 5000, pref, erros),
        }

        categoria = item.get("categoria_ameaca", "NE")
        if not isinstance(categoria, str) or categoria.strip().upper() not in CATEGORIAS_AMEACA:
            erros.add(f"{pref}: 'categoria_ameaca' inválida (use {', '.join(CATEGORIAS_AMEACA)}).")
        else:
            campos["categoria_ameaca"] = categoria.strip().upper()

        visivel = item.get("visivel", True)
        if not isinstance(visivel, bool):
            erros.add(f"{pref}: 'visivel' deve ser true ou false.")
        else:
            campos["visivel"] = visivel

        for campo in CAMPOS_OPCIONAIS:
            valor = item.get(campo)
            if valor is None:
                campos[campo] = None
            elif not isinstance(valor, str):
                erros.add(f"{pref}: '{campo}' deve ser texto ou null.")
            else:
                valor = valor.strip() or None
                if valor and len(valor) > MAX_OPCIONAL:
                    erros.add(f"{pref}: '{campo}' passa de {MAX_OPCIONAL} caracteres.")
                else:
                    campos[campo] = valor

        if erros.total > antes:
            continue
        if campos["nome"] in vistos:
            erros.add(f"{pref}: a espécie '{campos['nome']}' aparece repetida no arquivo.")
            continue
        vistos.add(campos["nome"])

        atual = existentes.get(campos["nome"])
        if atual is None:
            acao = "criar"
            cont["criados"] += 1
        elif any(getattr(atual, k) != v for k, v in campos.items()):
            acao = "atualizar"
            cont["atualizados"] += 1
        else:
            acao = "igual"
            cont["iguais"] += 1
        plano.append({"acao": acao, "campos": campos, "id": atual.id if atual else None})
    return plano, cont


def _aplicar_especies(sess, plano):
    for p in plano:
        if p["acao"] == "criar":
            sess.add(Especie(**p["campos"]))
        elif p["acao"] == "atualizar":
            especie = sess.get(Especie, p["id"])
            for campo, valor in p["campos"].items():
                setattr(especie, campo, valor)
    sess.flush()


def _planejar_tags(sess, itens, ctx, erros):
    existentes = {t.codigo: t for t in sess.query(Tag).all()}
    plano, vistos = [], set()
    cont = {"criados": 0, "atualizados": 0, "iguais": 0}

    for i, item in enumerate(itens, 1):
        pref = f"Item {i}"
        if not _chaves_ok(item, {"codigo", "nome"}, {"codigo", "nome"}, pref, erros):
            continue
        antes = erros.total
        nome = _texto(item, "nome", 200, pref, erros)
        codigo = item["codigo"]
        if not isinstance(codigo, str) or not RE_CODIGO.fullmatch(codigo):
            erros.add(f"{pref}: 'codigo' deve ter de 6 a 64 caracteres (letras, números, '-' ou '_').")
        if erros.total > antes:
            continue
        if codigo in vistos:
            erros.add(f"{pref}: o código '{codigo}' aparece repetido no arquivo.")
            continue
        vistos.add(codigo)

        atual = existentes.get(codigo)
        if atual is None:
            acao = "criar"
            cont["criados"] += 1
        elif atual.nome != nome:
            acao = "atualizar"
            cont["atualizados"] += 1
        else:
            acao = "igual"
            cont["iguais"] += 1
        plano.append({"acao": acao, "codigo": codigo, "nome": nome})

    # Nomes de tag precisam ser únicos (sem diferenciar maiúsculas) no estado FINAL
    final = {c: t.nome for c, t in existentes.items()}
    final.update({p["codigo"]: p["nome"] for p in plano})
    por_nome: dict[str, list[str]] = {}
    for codigo, nome in final.items():
        por_nome.setdefault(nome.casefold(), []).append(codigo)
    for codigos in por_nome.values():
        if len(codigos) > 1 and any(c in vistos for c in codigos):
            erros.add(f"O nome de tag '{final[codigos[0]]}' ficaria repetido "
                      f"(códigos: {', '.join(sorted(codigos))}).")
    return plano, cont


def _aplicar_tags(sess, plano):
    existentes = {t.codigo: t for t in sess.query(Tag).all()}
    renomear = [p for p in plano if p["acao"] == "atualizar"]
    # Renomeia em duas etapas para permitir trocar nomes entre tags sem violar a UNIQUE
    for p in renomear:
        tag = existentes[p["codigo"]]
        tag.nome = f"__renomeando__{tag.id}"
    sess.flush()
    for p in renomear:
        existentes[p["codigo"]].nome = p["nome"]
    sess.flush()
    for p in plano:
        if p["acao"] == "criar":
            sess.add(Tag(codigo=p["codigo"], nome=p["nome"]))
    sess.flush()


def _planejar_vinculos(sess, itens, ctx, erros):
    atual = {
        nome: codigo
        for nome, codigo in sess.query(Especie.nome, Tag.codigo).select_from(Vinculo)
        .join(Especie, Especie.id == Vinculo.especie_id)
        .join(Tag, Tag.id == Vinculo.tag_id).all()
    }
    plano, vistos = [], set()
    cont = {"criados": 0, "atualizados": 0, "iguais": 0}

    for i, item in enumerate(itens, 1):
        pref = f"Item {i}"
        if not _chaves_ok(item, {"especie", "tag"}, {"especie", "tag"}, pref, erros):
            continue
        especie, tag = item["especie"], item["tag"]
        antes = erros.total
        if not isinstance(especie, str) or especie not in ctx["especies"]:
            erros.add(f"{pref}: espécie {especie!r} não existe (cadastre-a ou importe as espécies junto).")
        if tag is not None and (not isinstance(tag, str) or tag not in ctx["tags"]):
            erros.add(f"{pref}: tag {tag!r} não existe (importe as tags junto ou crie-a antes).")
        if erros.total > antes:
            continue
        if especie in vistos:
            erros.add(f"{pref}: a espécie '{especie}' aparece repetida (cada espécie fica em uma só tag).")
            continue
        vistos.add(especie)

        if atual.get(especie) == tag:
            acao = "igual"
            cont["iguais"] += 1
        elif especie in atual:
            acao = "definir"
            cont["atualizados"] += 1
        else:
            acao = "definir" if tag is not None else "igual"
            cont["criados" if tag is not None else "iguais"] += 1
        plano.append({"acao": acao, "especie": especie, "tag": tag})
    return plano, cont


def _aplicar_vinculos(sess, plano):
    especies = {e.nome: e for e in sess.query(Especie).all()}
    tags = {t.codigo: t for t in sess.query(Tag).all()}
    for p in plano:
        if p["acao"] == "igual":
            continue
        tag = tags[p["tag"]] if p["tag"] is not None else None
        db._definir_tag_da_especie(sess, especies[p["especie"]], tag.id if tag else None)
    sess.flush()


def _planejar_usuarios(sess, itens, ctx, erros):
    existentes = {u.username: u for u in sess.query(Usuario).all()}
    plano, vistos = [], set()
    cont = {"criados": 0, "atualizados": 0, "iguais": 0}

    for i, item in enumerate(itens, 1):
        pref = f"Item {i}"
        obrig = {"username", "nome", "senha_hash"}
        if not _chaves_ok(item, obrig, obrig, pref, erros):
            continue
        antes = erros.total
        username = _texto(item, "username", 100, pref, erros)
        nome = _texto(item, "nome", 200, pref, erros)
        senha_hash = item["senha_hash"]
        if not isinstance(senha_hash, str) or not RE_HASH.fullmatch(senha_hash):
            erros.add(f"{pref}: 'senha_hash' não está no formato esperado.")
        if erros.total > antes:
            continue
        if username in vistos:
            erros.add(f"{pref}: o usuário '{username}' aparece repetido no arquivo.")
            continue
        vistos.add(username)

        atual = existentes.get(username)
        if atual is None:
            acao = "criar"
            cont["criados"] += 1
        elif atual.nome != nome or atual.senha_hash != senha_hash:
            acao = "atualizar"
            cont["atualizados"] += 1
        else:
            acao = "igual"
            cont["iguais"] += 1
        plano.append({"acao": acao, "username": username, "nome": nome,
                      "senha_hash": senha_hash, "id": atual.id if atual else None})
    return plano, cont


def _aplicar_usuarios(sess, plano):
    for p in plano:
        if p["acao"] == "criar":
            sess.add(Usuario(username=p["username"], nome=p["nome"], senha_hash=p["senha_hash"]))
        elif p["acao"] == "atualizar":
            usuario = sess.get(Usuario, p["id"])
            usuario.nome, usuario.senha_hash = p["nome"], p["senha_hash"]
    sess.flush()


def _data_iso(valor) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _planejar_acessos(sess, itens, ctx, erros):
    existentes = {
        (codigo, quando)
        for codigo, quando in sess.query(Tag.codigo, Acesso.criado_em)
        .select_from(Acesso).join(Tag, Tag.id == Acesso.tag_id).all()
    }
    novos, vistos = [], set()
    cont = {"criados": 0, "atualizados": 0, "iguais": 0}

    for i, item in enumerate(itens, 1):
        pref = f"Item {i}"
        if not _chaves_ok(item, {"tag", "criado_em"}, {"tag", "criado_em"}, pref, erros):
            continue
        antes = erros.total
        codigo = item["tag"]
        if not isinstance(codigo, str) or codigo not in ctx["tags"]:
            erros.add(f"{pref}: tag {codigo!r} não existe (importe as tags junto ou crie-a antes).")
        quando = _data_iso(item["criado_em"])
        if quando is None:
            erros.add(f"{pref}: 'criado_em' deve ser uma data ISO (ex.: 2026-10-31T12:00:00Z).")
        if erros.total > antes:
            continue
        chave = (codigo, quando)
        if chave in existentes or chave in vistos:
            cont["iguais"] += 1  # já registrado: evita duplicar ao reimportar
            continue
        vistos.add(chave)
        novos.append(chave)
        cont["criados"] += 1
    return novos, cont


def _aplicar_acessos(sess, novos):
    if not novos:
        return
    tags = {t.codigo: t.id for t in sess.query(Tag).all()}
    linhas = [{"tag_id": tags[codigo], "criado_em": quando} for codigo, quando in novos]
    sess.execute(insert(Acesso), linhas)
    sess.flush()


PLANEJADORES = {
    "especies": _planejar_especies, "tags": _planejar_tags, "vinculos": _planejar_vinculos,
    "usuarios": _planejar_usuarios, "acessos": _planejar_acessos,
}
APLICADORES = {
    "especies": _aplicar_especies, "tags": _aplicar_tags, "vinculos": _aplicar_vinculos,
    "usuarios": _aplicar_usuarios, "acessos": _aplicar_acessos,
}


# ═════════════════════════════ Importação ═════════════════════════════

def importar(arquivos: list[tuple[str, bytes]], simular: bool = False) -> list[dict]:
    """
    Importa vários arquivos de uma vez. `arquivos` = [(nome_original, conteúdo)].
    Cada arquivo é identificado pelo CONTEÚDO (campo "tipo"), não pelo nome.
    Com simular=True só valida e conta, sem gravar nada.

    Retorna uma lista (na ordem recebida) com, para cada arquivo:
      {"arquivo", "tipo", "rotulo", "status", "criados", "atualizados", "iguais", "erros"}
    status: "aplicado" | "validado" (simulação) | "rejeitado"
    """
    resultados, lidos = [], {}

    for nome, conteudo in arquivos:
        res = {"arquivo": nome, "tipo": None, "rotulo": None, "status": "rejeitado",
               "criados": 0, "atualizados": 0, "iguais": 0, "erros": []}
        resultados.append(res)

        tipo, itens, erros = _ler_envelope(conteudo)
        if erros:
            res["erros"] = erros.lista()
            continue
        res["tipo"], res["rotulo"] = tipo, TIPOS[tipo]["rotulo"]
        if tipo in lidos:
            res["erros"] = [f"Já existe outro arquivo de {TIPOS[tipo]['rotulo']} neste envio."]
            continue
        lidos[tipo] = (res, itens)

    sess = db.get_db()
    try:
        ctx = {
            "especies": {nome for (nome,) in sess.query(Especie.nome).all()},
            "tags": {codigo for (codigo,) in sess.query(Tag.codigo).all()},
        }

        aplicaveis = []
        for tipo in TIPOS:  # ordem de dependência
            if tipo not in lidos:
                continue
            res, itens = lidos[tipo]
            erros = _Erros()
            plano, cont = PLANEJADORES[tipo](sess, itens, ctx, erros)
            if erros:
                res["erros"] = erros.lista()
                continue
            res.update(cont)
            res["status"] = "validado" if simular else "aplicado"
            if tipo == "especies":
                ctx["especies"] |= {p["campos"]["nome"] for p in plano}
            elif tipo == "tags":
                ctx["tags"] |= {p["codigo"] for p in plano}
            aplicaveis.append((tipo, res, plano))

        if aplicaveis and not simular:
            try:
                for tipo, _res, plano in aplicaveis:
                    APLICADORES[tipo](sess, plano)
                sess.commit()
            except Exception:
                sess.rollback()
                log.exception("Falha ao gravar a importação")
                for _tipo, res, _plano in aplicaveis:
                    res["status"] = "rejeitado"
                    res["criados"] = res["atualizados"] = res["iguais"] = 0
                    res["erros"] = ["Falha inesperada ao gravar. Nada foi alterado."]
    finally:
        sess.close()

    return resultados