"""
Processamento e armazenamento seguro das imagens das espécies.

Princípios de segurança:
  * O arquivo enviado NUNCA é guardado nem servido como veio. Ele é decodificado,
    redimensionado e recodificado em WebP. Isso descarta metadados (EXIF/GPS),
    dados escondidos no final do arquivo e arquivos "poliglota".
  * O formato é validado pelo CONTEÚDO (não pela extensão nem pelo Content-Type),
    e só os decodificadores de JPEG, PNG e WebP são habilitados.
  * Limites de tamanho do arquivo, de dimensões (anti "bomba de descompressão")
    e de quantidade de imagens.
  * Os arquivos ficam fora de static/ e têm nome aleatório (o nome original
    enviado pelo usuário nunca é usado em caminho de arquivo).

Este módulo não conhece o banco de dados.
"""
import io
import logging
import os
import re
import secrets
from dataclasses import dataclass

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

# ─── Limites ──────────────────────────────────────────────────────────────────
MAX_BYTES = 5 * 1024 * 1024        # tamanho máximo de cada arquivo enviado
MAX_POR_ENVIO = 6                  # imagens por envio (um submit do formulário)
MAX_POR_ESPECIE = 20               # imagens por espécie, no total
MAX_PIXELS = 40_000_000            # ~7000 x 5700 (anti bomba de descompressão)
MIN_LADO = 64                      # lado mínimo, em pixels
MAX_LEGENDA = 120
LADO_GRANDE = 1600                 # versão exibida na galeria
LADO_MINI = 400                    # miniatura
FORMATOS_ACEITOS = ("JPEG", "PNG", "WEBP")

# O Pillow também avisa/erra por conta própria acima disso (defesa em profundidade)
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

TOKEN_RE = re.compile(r"[0-9a-f]{32}")
TAMANHOS = ("g", "m")              # g = grande, m = miniatura


class ErroImagem(ValueError):
    """Imagem recusada (a mensagem pode ser mostrada ao usuário)."""


@dataclass
class Processada:
    grande: bytes
    miniatura: bytes
    largura: int
    altura: int


# ─── Processamento ────────────────────────────────────────────────────────────

def _webp(img: Image.Image, qualidade: int) -> bytes:
    # Sem passar exif/icc: nenhum metadado do original é preservado
    buffer = io.BytesIO()
    img.save(buffer, "WEBP", quality=qualidade, method=4)
    return buffer.getvalue()


def processar(dados: bytes, nome: str = "imagem") -> Processada:
    """Valida e converte uma imagem enviada. Levanta ErroImagem se for recusada."""
    rotulo = f"'{nome}'"
    if not dados:
        raise ErroImagem(f"{rotulo}: o arquivo está vazio.")
    if len(dados) > MAX_BYTES:
        raise ErroImagem(f"{rotulo}: passa de {MAX_BYTES // (1024 * 1024)} MB.")

    try:
        img = Image.open(io.BytesIO(dados), formats=FORMATOS_ACEITOS)
        largura, altura = img.size  # só lê o cabeçalho; ainda não decodificou nada
        if largura * altura > MAX_PIXELS:
            raise ErroImagem(
                f"{rotulo}: dimensões grandes demais ({largura}x{altura}). "
                f"O máximo é {MAX_PIXELS // 1_000_000} megapixels."
            )
        if min(largura, altura) < MIN_LADO:
            raise ErroImagem(f"{rotulo}: muito pequena (mínimo {MIN_LADO}x{MIN_LADO} pixels).")

        img.load()  # decodifica (se for animada, só o primeiro quadro)
        img = ImageOps.exif_transpose(img)  # aplica a rotação e descarta o EXIF
        tem_alfa = img.mode in ("RGBA", "LA", "PA") or (
            img.mode == "P" and "transparency" in img.info
        )
        img = img.convert("RGBA" if tem_alfa else "RGB")

        grande = img.copy()
        grande.thumbnail((LADO_GRANDE, LADO_GRANDE), Image.Resampling.LANCZOS)
        mini = grande.copy()
        mini.thumbnail((LADO_MINI, LADO_MINI), Image.Resampling.LANCZOS)

        return Processada(
            grande=_webp(grande, 82),
            miniatura=_webp(mini, 76),
            largura=grande.width,
            altura=grande.height,
        )
    except ErroImagem:
        raise
    except Exception as e:  # formato inválido, arquivo corrompido/truncado, bomba, etc.
        log.info("Imagem recusada (%s): %s: %s", nome, type(e).__name__, e)
        raise ErroImagem(f"{rotulo}: não é uma imagem JPEG, PNG ou WebP válida.") from None


def _nome_seguro(nome: str) -> str:
    nome = os.path.basename((nome or "").replace("\\", "/"))[:60]
    return "".join(c for c in nome if c.isprintable()) or "imagem"


def processar_envio(arquivos) -> list[Processada]:
    """
    Processa os arquivos de um campo <input type="file" multiple> (objetos
    FileStorage do Flask). Campos vazios são ignorados. É tudo ou nada: se uma
    imagem for recusada, levanta ErroImagem e nada é aproveitado.
    """
    validos = [a for a in arquivos if a and a.filename]
    if len(validos) > MAX_POR_ENVIO:
        raise ErroImagem(f"Envie no máximo {MAX_POR_ENVIO} imagens por vez.")
    resultado = []
    for arquivo in validos:
        dados = arquivo.stream.read(MAX_BYTES + 1)  # nunca lê além do limite
        resultado.append(processar(dados, _nome_seguro(arquivo.filename)))
    return resultado


# ─── Armazenamento ────────────────────────────────────────────────────────────

def pasta() -> str:
    """Pasta das imagens (variável UPLOAD_DIR, ou ./uploads ao lado do programa)."""
    base = os.environ.get("UPLOAD_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "uploads"
    )
    destino = os.path.join(os.path.abspath(base), "imagens")
    os.makedirs(destino, exist_ok=True)
    return destino


def _remover_arquivo(caminho: str) -> None:
    try:
        os.remove(caminho)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("Não foi possível apagar %s", caminho)


def salvar(proc: Processada) -> str:
    """Grava as duas versões em disco e devolve o token (nome aleatório)."""
    token = secrets.token_hex(16)
    diretorio = pasta()
    criados = []
    try:
        for sufixo, conteudo in (("g", proc.grande), ("m", proc.miniatura)):
            destino = os.path.join(diretorio, f"{token}_{sufixo}.webp")
            temporario = destino + ".tmp"
            criados.append(temporario)
            with open(temporario, "wb") as f:
                f.write(conteudo)
            os.replace(temporario, destino)  # escrita atômica
            criados[-1] = destino
    except Exception:
        for caminho in criados:
            _remover_arquivo(caminho)
        raise
    return token


def apagar(tokens) -> None:
    """Apaga os arquivos dos tokens informados (tokens inválidos são ignorados)."""
    if not tokens:
        return
    diretorio = pasta()
    for token in tokens or []:
        if isinstance(token, str) and TOKEN_RE.fullmatch(token):
            for sufixo in TAMANHOS:
                _remover_arquivo(os.path.join(diretorio, f"{token}_{sufixo}.webp"))


def localizar(token: str, tamanho: str):
    """(pasta, nome do arquivo) se token e tamanho forem válidos; senão None."""
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token) or tamanho not in TAMANHOS:
        return None
    return pasta(), f"{token}_{tamanho}.webp"