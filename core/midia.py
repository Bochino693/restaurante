"""Fotos e arquivos enviados guardados no próprio banco do site.

Por quê: a Cloudinary (onde as fotos ficavam) foi desativada e levou junto
todas as imagens e os envios novos. Agora o arquivo mora numa tabela do
banco (core.ArquivoNoBanco) e é servido pelo próprio site em
/midia/banco/<caminho>, com cache longo na borda (Vercel) e no navegador.
Nenhum serviço de fora participa: se o site está no ar, a foto está no ar.

Nomes:
- "banco/<caminho original>" -> arquivo no banco (ex.: banco/produtos/coca-1a2b3c4d5e.webp).
- qualquer outro nome -> legado (Cloudinary, ou o disco no desenvolvimento).
  O legado só é usado para montar o endereço antigo; nada aqui depende dele.

O endereço de um arquivo no banco nunca muda de conteúdo (o nome leva um
pedaço do sha256), por isso pode ficar um ano em cache sem medo.
"""
import hashlib
import io
import logging
import mimetypes
import os
import posixpath
import re
import secrets
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile, File
from django.core.files.storage import FileSystemStorage, Storage
from django.core.files.utils import validate_file_name
from django.db import IntegrityError, transaction
from django.utils.deconstruct import deconstructible
from django.utils.encoding import filepath_to_uri
from django.utils.functional import cached_property

logger = logging.getLogger(__name__)

PREFIXO = "banco/"
URL_PREFIXO = "/midia/banco/"

# Foto maior que isso (lado ou peso) é regravada em WebP: fica leve para o
# cardápio no celular e não enche o banco.
LADO_MAXIMO = 2000
LIMITE_BYTES = 600 * 1024
QUALIDADE_WEBP = 82
QUALIDADE_MINIATURA = 80

# Larguras aceitas em "?w=": poucas, para a borda guardar poucas versões e
# ninguém conseguir obrigar o servidor a redimensionar à toa.
LARGURAS = (96, 160, 240, 320, 480, 640, 800, 960, 1280, 1600)

TIPOS_DO_PILLOW = {
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",  # foto de celular "JPEG com várias imagens"
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
    "ICO": "image/x-icon",
}

_EXTENSAO = re.compile(r"^\.[A-Za-z0-9]{2,5}$")


class ErroAoGuardarMidia(Exception):
    """O arquivo não pôde ser guardado. A mensagem já é para o usuário."""

    MENSAGEM = (
        "Não foi possível guardar a foto agora. Nada foi alterado: tente "
        "enviar de novo em alguns instantes. Se continuar, use uma foto menor."
    )

    def __init__(self, mensagem=None):
        super().__init__(mensagem or self.MENSAGEM)


def largura_permitida(largura):
    """A menor largura da lista que cobre a pedida (ou a maior da lista)."""
    for permitida in LARGURAS:
        if largura <= permitida:
            return permitida
    return LARGURAS[-1]


def extensao_de(nome):
    """Extensão de verdade (".jpg"), ou "" (ex.: o id da Cloudinary
    "media/produtos/coca_ab12cd" não tem extensão)."""
    extensao = posixpath.splitext(nome)[1]
    return extensao if _EXTENSAO.match(extensao) else ""


def tipo_pelo_nome(nome):
    return mimetypes.guess_type(nome)[0] or "application/octet-stream"


# ------------------------------------------------------------- imagens

def _abrir_imagem(dados):
    from PIL import Image

    imagem = Image.open(io.BytesIO(dados))
    return imagem


def _perfil_de_cor(imagem):
    """Perfil de cor para manter as cores do celular (o de CMYK não serve
    depois da conversão para RGB)."""
    return None if imagem.mode == "CMYK" else imagem.info.get("icc_profile")


def _modo_para_webp(imagem):
    tem_transparencia = imagem.mode in ("RGBA", "LA", "PA") or (
        imagem.mode == "P" and "transparency" in imagem.info
    )
    alvo = "RGBA" if tem_transparencia else "RGB"
    return imagem if imagem.mode == alvo else imagem.convert(alvo)


def _gravar_webp(imagem, qualidade, icc=None):
    saida = io.BytesIO()
    opcoes = {"format": "WEBP", "quality": qualidade, "method": 4}
    if icc:
        opcoes["icc_profile"] = icc
    imagem.save(saida, **opcoes)
    return saida.getvalue()


def preparar_arquivo(dados, nome):
    """(dados, tipo, extensão) do que vai para o banco.

    Foto grande (lado > 2000 px ou mais de ~600 KB) vira WebP de no máximo
    2000 px, boa qualidade, já girada como o celular tirou (EXIF) e sem os
    dados de localização. O resto vai como veio. Qualquer surpresa do
    Pillow: guarda o original, nunca recusa por isso.
    """
    from PIL import Image, ImageOps

    extensao = extensao_de(nome).lower()
    try:
        with _abrir_imagem(dados) as imagem:
            tipo = TIPOS_DO_PILLOW.get(imagem.format) or Image.MIME.get(imagem.format or "", "")
            tipo = tipo or tipo_pelo_nome(nome)
            grande = max(imagem.size) > LADO_MAXIMO or len(dados) > LIMITE_BYTES
            if not grande or getattr(imagem, "is_animated", False):
                return dados, tipo, extensao
            ja_cabe = max(imagem.size) <= LADO_MAXIMO
            icc = _perfil_de_cor(imagem)
            imagem.draft("RGB", (LADO_MAXIMO, LADO_MAXIMO))  # JPEG: decodifica já reduzido
            reduzida = ImageOps.exif_transpose(imagem)
            reduzida.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)
            webp = _gravar_webp(_modo_para_webp(reduzida), QUALIDADE_WEBP, icc)
    except Exception:  # não é imagem, imagem estranha, bomba de pixels...
        return dados, tipo_pelo_nome(nome), extensao
    if ja_cabe and len(webp) >= len(dados):
        return dados, tipo, extensao
    return webp, "image/webp", ".webp"


def miniatura(dados, largura):
    """WebP com a largura pedida, ou None quando não vale (a foto já é
    menor, é animada ou o Pillow não abriu): aí vai a original."""
    from PIL import Image, ImageOps

    try:
        with _abrir_imagem(dados) as imagem:
            if getattr(imagem, "is_animated", False):
                return None
            # Antes de girar, o lado menor precisa cobrir a largura pedida.
            imagem.draft("RGB", (largura, largura))
            girada = ImageOps.exif_transpose(imagem)
            if girada.width <= largura:
                return None
            altura = max(1, round(girada.height * largura / girada.width))
            reduzida = _modo_para_webp(girada).resize((largura, altura), Image.LANCZOS)
            return _gravar_webp(reduzida, QUALIDADE_MINIATURA, _perfil_de_cor(imagem))
    except Exception:
        logger.warning("Não deu para reduzir a foto; vai a original.", exc_info=True)
        return None


# --------------------------------------------------------------- nomes

def nome_no_banco(nome, sha256, extensao, max_length=None):
    """banco/<pasta>/<nome>-<10 do sha256><extensão>, cabendo no campo."""
    nome = nome.replace("\\", "/").lstrip("/")
    if nome.startswith(PREFIXO):
        nome = nome[len(PREFIXO):]
    pasta, arquivo = posixpath.split(nome)
    raiz = posixpath.splitext(arquivo)[0] if extensao_de(arquivo) else arquivo
    raiz = raiz or "arquivo"
    sufixo = f"-{sha256[:10]}{extensao}"
    inicio = PREFIXO + (f"{pasta}/" if pasta else "")
    limite = min(max_length or 255, 255)
    sobra = limite - len(inicio) - len(sufixo)
    if sobra < 1:
        raise SuspiciousFileOperation(f'O nome "{nome}" não cabe em {limite} caracteres.')
    return inicio + raiz[:sobra] + sufixo


def guardar_bytes(nome, dados, tipo):
    """Grava (ou reaproveita) o arquivo com exatamente este nome.

    Mesmo nome com o mesmo conteúdo: reaproveita (enviar a mesma foto duas
    vezes não duplica nada). Mesmo nome com outro conteúdo: devolve False
    (quem chamou escolhe outro nome).
    """
    from .models import ArquivoNoBanco

    sha256 = hashlib.sha256(dados).hexdigest()
    existente = ArquivoNoBanco.objects.filter(nome=nome).values_list("sha256", flat=True).first()
    if existente is not None:
        return existente == sha256
    try:
        with transaction.atomic():
            ArquivoNoBanco.objects.create(
                nome=nome, conteudo=dados, tipo=tipo[:100], tamanho=len(dados), sha256=sha256,
            )
    except IntegrityError:  # duas gravações ao mesmo tempo
        existente = ArquivoNoBanco.objects.filter(nome=nome).values_list("sha256", flat=True).first()
        return existente == sha256
    return True


def ler_conteudo(content):
    if hasattr(content, "seek"):
        try:
            content.seek(0)
        except Exception:
            pass
    return b"".join(content.chunks()) if hasattr(content, "chunks") else content.read()


# ------------------------------------------------------------- storage

@deconstructible
class MidiaNoBanco(Storage):
    """Armazenamento padrão em produção: grava no banco, lê do banco os nomes
    "banco/..." e deixa os nomes antigos com o legado (só para o endereço)."""

    @cached_property
    def legado(self):
        if (getattr(settings, "CLOUDINARY_STORAGE", None) or {}).get("CLOUD_NAME"):
            try:
                from cloudinary_storage.storage import MediaCloudinaryStorage

                return MediaCloudinaryStorage()
            except Exception:
                logger.warning("Cloudinary indisponível; o legado fica no disco.", exc_info=True)
        return FileSystemStorage()

    @staticmethod
    def no_banco(name):
        return bool(name) and str(name).startswith(PREFIXO)

    # gravar -------------------------------------------------------------
    def save(self, name, content, max_length=None):
        if name is None:
            name = content.name
        if not hasattr(content, "chunks"):
            content = File(content, name)
        validate_file_name(name, allow_relative_path=True)
        try:
            nome = self._guardar(name, content, max_length)
        except SuspiciousFileOperation:
            raise
        except ErroAoGuardarMidia:
            raise
        except Exception as erro:
            logger.exception("Falha ao guardar %s no banco", name)
            raise ErroAoGuardarMidia() from erro
        validate_file_name(nome, allow_relative_path=True)
        return nome

    def _save(self, name, content):
        return self._guardar(name, content, None)

    def _guardar(self, name, content, max_length):
        dados, tipo, extensao = preparar_arquivo(ler_conteudo(content), name)
        sha256 = hashlib.sha256(dados).hexdigest()
        nome = nome_no_banco(name, sha256, extensao, max_length)
        for _ in range(5):
            if guardar_bytes(nome, dados, tipo):
                return nome
            # Mesmo nome, outro conteúdo (quase impossível): sorteia outro.
            nome = nome_no_banco(f"{posixpath.splitext(name)[0]}_{secrets.token_hex(3)}", sha256, extensao, max_length)
        raise ErroAoGuardarMidia()

    # ler ----------------------------------------------------------------
    def _open(self, name, mode="rb"):
        if not self.no_banco(name):
            return self.legado.open(name, mode)
        from .models import ArquivoNoBanco

        conteudo = ArquivoNoBanco.objects.filter(nome=name).values_list("conteudo", flat=True).first()
        if conteudo is None:
            raise FileNotFoundError(name)
        arquivo = ContentFile(bytes(conteudo), name=name)
        arquivo.mode = mode
        return arquivo

    def exists(self, name):
        if not self.no_banco(name):
            try:
                return self.legado.exists(name)
            except Exception:
                return False
        from .models import ArquivoNoBanco

        return ArquivoNoBanco.objects.filter(nome=name).exists()

    def size(self, name):
        if not self.no_banco(name):
            return self.legado.size(name)
        from .models import ArquivoNoBanco

        tamanho = ArquivoNoBanco.objects.filter(nome=name).values_list("tamanho", flat=True).first()
        if tamanho is None:
            raise FileNotFoundError(name)
        return tamanho

    def delete(self, name):
        if not name:
            return
        if not self.no_banco(name):
            try:
                self.legado.delete(name)
            except Exception:
                logger.warning("Não deu para apagar %s no legado.", name, exc_info=True)
            return
        from .models import ArquivoNoBanco

        ArquivoNoBanco.objects.filter(nome=name).delete()

    def listdir(self, path):
        from .models import ArquivoNoBanco

        base = path.rstrip("/") + "/" if path else ""
        pastas, arquivos = set(), []
        for nome in ArquivoNoBanco.objects.filter(nome__startswith=base).values_list("nome", flat=True):
            resto = nome[len(base):]
            if "/" in resto:
                pastas.add(resto.split("/", 1)[0])
            else:
                arquivos.append(resto)
        return sorted(pastas), arquivos

    def get_created_time(self, name):
        from .models import ArquivoNoBanco

        criado = ArquivoNoBanco.objects.filter(nome=name).values_list("criado_em", flat=True).first()
        if criado is None:
            raise FileNotFoundError(name)
        return criado

    get_modified_time = get_created_time

    # endereço (sem rede e sem banco) -------------------------------------
    def url(self, name):
        if self.no_banco(name):
            return URL_PREFIXO + filepath_to_uri(name[len(PREFIXO):])
        if str(name).startswith(("http://", "https://")):
            return name
        try:
            return self.legado.url(name)
        except Exception:
            logger.warning("Sem endereço para o arquivo antigo %s.", name, exc_info=True)
            return settings.MEDIA_URL + filepath_to_uri(name)


def url_da_foto(campo, largura=None):
    """Endereço da foto de um ImageField, já com "?w=" para as fotos do
    banco; "" quando não há foto ou o endereço não pôde ser montado."""
    try:
        if not campo:
            return ""
        url = campo.url
    except Exception:
        return ""
    if largura and str(campo.name).startswith(PREFIXO):
        url = f"{url}?w={largura_permitida(int(largura))}"
    return url


# ------------------------------------------------- cache em disco (servidor)

def pasta_do_cache():
    pasta = getattr(settings, "MIDIA_CACHE_PASTA", "")
    return Path(pasta) if pasta else None


def ler_do_cache(sha256, largura):
    pasta = pasta_do_cache()
    if not pasta:
        return None
    try:
        bruto = (pasta / f"{sha256}-{largura or 0}").read_bytes()
        tipo, _, dados = bruto.partition(b"\n")
        return dados, tipo.decode()
    except (OSError, UnicodeDecodeError):
        return None


def gravar_no_cache(sha256, largura, dados, tipo):
    pasta = pasta_do_cache()
    if not pasta:
        return
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        final = pasta / f"{sha256}-{largura or 0}"
        with tempfile.NamedTemporaryFile(dir=pasta, delete=False) as temporario:
            temporario.write(tipo.encode() + b"\n" + dados)
        os.replace(temporario.name, final)
    except OSError:
        logger.warning("Não deu para guardar a foto no cache do disco.", exc_info=True)
