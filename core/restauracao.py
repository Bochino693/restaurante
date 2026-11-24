"""Trazer de volta para o banco as fotos antigas (as da Cloudinary).

Três origens, a mesma regra:
- cópias que já estão no projeto (pastas produtos/, imagens_categorias/,
  media/...): `restaurar_copias_locais()`, roda sozinho depois do `migrate`;
- o ZIP baixado da Cloudinary ou fotos soltas: página "Restaurar fotos" e
  `manage.py restaurar_midia --zip`;
- a própria Cloudinary, quando a conta voltar: `manage.py espelhar_midia`.

Cada arquivo vira "banco/<nome antigo>" e todo campo que usava o nome antigo
passa a apontar para ele. Regra de ouro: na dúvida, NÃO associa (foto
errada num produto é pior que a foto em atualização). A associação é feita
pelo id público da Cloudinary (pasta + nome sem extensão); o nome sem o
sufixo de 6 letras que a Cloudinary acrescenta só vale quando um único
arquivo e uma única referência batem.
"""
import hashlib
import io
import logging
import os
import posixpath
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.apps import apps
from django.conf import settings
from django.db import models, transaction

from .midia import PREFIXO, extensao_de, guardar_bytes, nome_no_banco, preparar_arquivo

logger = logging.getLogger(__name__)

SUFIXO_CLOUDINARY = re.compile(r"^(?P<raiz>.+)_[a-z0-9]{6}$")
FAMILIA_JPEG = {".jpg", ".jpeg", ".jpe", ".jfif"}
LIMITE_ARQUIVO = 60 * 1024 * 1024        # uma foto (ou entrada do ZIP)
LIMITE_ZIP = 500 * 1024 * 1024           # ZIP inteiro enviado de uma vez
LIMITE_ENVIO_SERVERLESS = 4 * 1024 * 1024  # corpo de requisição na Vercel (~4,5 MB)

# Quanto mais alto, mais certeza.
EXATO, MESMO_NOME, SEM_SUFIXO = 3, 2, 1


def limite_de_envio():
    """Maior arquivo aceito numa requisição do site."""
    return LIMITE_ENVIO_SERVERLESS if getattr(settings, "SERVERLESS", False) else LIMITE_ZIP


# --------------------------------------------------------------- referências

@dataclass(frozen=True)
class Referencia:
    modelo: type
    pk: object
    campo: models.FileField
    nome: str

    @property
    def descricao(self):
        return f"{self.modelo._meta.verbose_name} #{self.pk} · {self.campo.name}"


def campos_de_arquivo():
    for modelo in apps.get_models():
        for campo in modelo._meta.concrete_fields:
            if isinstance(campo, models.FileField):
                yield modelo, campo


def referencias(incluir_banco=False):
    """{nome do arquivo: [Referencia, ...]} de todo FileField/ImageField.
    Por padrão só os nomes antigos (fora do banco)."""
    achadas = defaultdict(list)
    for modelo, campo in campos_de_arquivo():
        linhas = (
            modelo._base_manager.exclude(**{campo.attname: ""})
            .exclude(**{f"{campo.attname}__isnull": True})
            .values_list("pk", campo.attname)
        )
        for pk, nome in linhas:
            if nome.startswith(PREFIXO) and not incluir_banco:
                continue
            achadas[nome].append(Referencia(modelo, pk, campo, nome))
    return dict(achadas)


def faltando():
    """Referências sem foto no banco: nome antigo, ou "banco/..." que sumiu."""
    from .models import ArquivoNoBanco

    todas = referencias(incluir_banco=True)
    no_banco = [nome for nome in todas if nome.startswith(PREFIXO)]
    existentes = set(ArquivoNoBanco.objects.filter(nome__in=no_banco).values_list("nome", flat=True))
    lista = [
        ref
        for nome, refs in todas.items()
        if not nome.startswith(PREFIXO) or nome not in existentes
        for ref in refs
    ]
    return sorted(lista, key=lambda r: (r.modelo._meta.label, str(r.pk), r.campo.name))


def descrever(refs):
    """Linhas para a tela/relatório, com o nome do produto/categoria."""
    por_modelo = defaultdict(set)
    for ref in refs:
        por_modelo[ref.modelo].add(ref.pk)
    rotulos = {}
    for modelo, pks in por_modelo.items():
        for objeto in modelo._base_manager.filter(pk__in=pks):
            rotulos[(modelo, objeto.pk)] = str(objeto)
    return [
        {
            "modelo": str(ref.modelo._meta.verbose_name),
            "chave": ref.modelo._meta.label,
            "pk": ref.pk,
            "campo": ref.campo.name,
            "nome": ref.nome,
            "rotulo": rotulos.get((ref.modelo, ref.pk), ""),
        }
        for ref in refs
    ]


# ------------------------------------------------------------------ nomes

def _prefixos_de_midia():
    prefixos = {"media/"}
    prefixo = ((getattr(settings, "CLOUDINARY_STORAGE", None) or {}).get("PREFIX") or settings.MEDIA_URL or "")
    prefixo = prefixo.strip("/")
    if prefixo and "://" not in prefixo:
        prefixos.add(prefixo + "/")
    return prefixos


def identidade(nome):
    """(pasta, raiz, extensão) de um nome do banco ou caminho de arquivo,
    sem "banco/", sem o prefixo de mídia ("media/") e sem extensão.
    Ex.: "media/produtos/coca_ab12cd" -> ("produtos", "coca_ab12cd", "")."""
    texto = str(nome).strip().replace("\\", "/")
    if "://" in texto:  # endereço completo da Cloudinary guardado no campo
        caminho = urlsplit(texto).path
        if "/upload/" in caminho:
            caminho = caminho.split("/upload/", 1)[1]
        texto = unquote(re.sub(r"^v\d+/", "", caminho.lstrip("/")))
    texto = texto.lstrip("/")
    if texto.startswith(PREFIXO):
        texto = texto[len(PREFIXO):]
    for prefixo in _prefixos_de_midia():
        if texto.startswith(prefixo):
            texto = texto[len(prefixo):]
            break
    pasta, arquivo = posixpath.split(texto)
    extensao = extensao_de(arquivo)
    raiz = arquivo[: -len(extensao)] if extensao else arquivo
    return pasta, raiz, extensao.lower()


def _pastas_combinam(do_arquivo, da_referencia):
    """A pasta do arquivo é a da referência (ou termina nela: o ZIP pode vir
    dentro de outra pasta)."""
    return do_arquivo == da_referencia or do_arquivo.endswith("/" + da_referencia)


def _extensoes_combinam(da_referencia, do_arquivo):
    if not da_referencia:  # id da Cloudinary não tem extensão
        return True
    if da_referencia == do_arquivo:
        return True
    return da_referencia in FAMILIA_JPEG and do_arquivo in FAMILIA_JPEG


@dataclass
class Achado:
    nivel: int = 0
    nomes: tuple = ()
    motivo: str = ""


@dataclass(frozen=True)
class _Entrada:
    nome: str
    pasta: str
    raiz: str
    extensao: str


class Indice:
    """Procura, para um caminho de arquivo, o nome antigo que ele restaura."""

    def __init__(self, nomes):
        self.por_raiz = defaultdict(list)
        self.por_raiz_sem_sufixo = defaultdict(list)
        for nome in nomes:
            entrada = _Entrada(nome, *identidade(nome))
            self.por_raiz[entrada.raiz].append(entrada)
            sufixo = SUFIXO_CLOUDINARY.match(entrada.raiz)
            # Só id da Cloudinary (sem extensão) tem o sufixo sorteado.
            if sufixo and not entrada.extensao:
                self.por_raiz_sem_sufixo[sufixo.group("raiz")].append(entrada)

    @staticmethod
    def _nomes(entradas):
        return tuple(sorted({e.nome for e in entradas}))

    def procurar(self, caminho):
        pasta, raiz, extensao = identidade(caminho)
        if not raiz:
            return Achado()
        candidatos = [e for e in self.por_raiz.get(raiz, ()) if _extensoes_combinam(e.extensao, extensao)]

        # 1) Id público inteiro (pasta + nome): certeza.
        exatos = [
            e for e in candidatos
            if (pasta and e.pasta and _pastas_combinam(pasta, e.pasta)) or (not pasta and not e.pasta)
        ]
        if exatos:
            return Achado(EXATO, self._nomes(exatos))

        # 2) Arquivo solto (sem pasta) com o nome inteiro: o sufixo sorteado
        #    da Cloudinary torna o nome único; dois iguais = dúvida.
        if not pasta and candidatos:
            nomes = self._nomes(candidatos)
            if len({identidade(n)[:2] for n in nomes}) == 1:
                return Achado(MESMO_NOME, nomes)
            return Achado(0, nomes, "mais de uma foto antiga com este nome")

        # 3) Nome original, sem o sufixo da Cloudinary ("coca.jpg" para
        #    "produtos/coca_ab12cd"): só se for a única referência possível.
        curtos = [
            e for e in self.por_raiz_sem_sufixo.get(raiz, ())
            if not pasta or _pastas_combinam(pasta, e.pasta)
        ]
        if curtos:
            nomes = self._nomes(curtos)
            if len({identidade(n)[:2] for n in nomes}) == 1:
                return Achado(SEM_SUFIXO, nomes)
            return Achado(0, nomes, "mais de uma foto antiga com este nome (envie o ZIP da Cloudinary)")
        return Achado()


def associar(caminhos, nomes, conteudo=None):
    """{caminho: Achado} para um lote de arquivos, sem conflito: um nome
    antigo fica com o arquivo de maior certeza; empate entre arquivos
    diferentes = dúvida para todos (a não ser que o conteúdo seja igual,
    quando `conteudo(caminho)` é informado)."""
    indice = Indice(nomes)
    achados = {caminho: indice.procurar(caminho) for caminho in caminhos}
    disputas = defaultdict(list)
    for caminho, achado in achados.items():
        for nome in achado.nomes if achado.nivel else ():
            disputas[nome].append(caminho)

    perdeu = defaultdict(set)
    for nome, concorrentes in disputas.items():
        if len(concorrentes) < 2:
            continue
        melhor = max(achados[c].nivel for c in concorrentes)
        primeiros = [c for c in concorrentes if achados[c].nivel == melhor]
        for caminho in concorrentes:
            if achados[caminho].nivel < melhor:
                perdeu[caminho].add(nome)
        if len(primeiros) > 1:
            iguais = False
            if conteudo:
                try:
                    iguais = len({hashlib.sha256(conteudo(c)).hexdigest() for c in primeiros}) == 1
                except Exception:
                    iguais = False
            vencedores = primeiros[:1] if iguais else []
            for caminho in primeiros:
                if caminho not in vencedores:
                    perdeu[caminho].add(nome)

    resultado = {}
    for caminho, achado in achados.items():
        if not achado.nivel:
            resultado[caminho] = achado
            continue
        restantes = tuple(n for n in achado.nomes if n not in perdeu[caminho])
        if restantes:
            resultado[caminho] = Achado(achado.nivel, restantes)
        else:
            resultado[caminho] = Achado(0, achado.nomes, "outro arquivo do lote também serve para esta foto")
    return resultado


# ------------------------------------------------------------ restaurar

class ArquivoRecusado(Exception):
    pass


@dataclass
class Relatorio:
    restauradas: list = field(default_factory=list)      # (caminho, nome antigo, nome novo, usos)
    ja_restauradas: list = field(default_factory=list)   # caminho
    nao_referenciados: list = field(default_factory=list)  # caminho
    duvidas: list = field(default_factory=list)          # (caminho, motivo)
    erros: list = field(default_factory=list)            # (caminho, mensagem)

    def juntar(self, outro):
        for nome in ("restauradas", "ja_restauradas", "nao_referenciados", "duvidas", "erros"):
            getattr(self, nome).extend(getattr(outro, nome))
        return self

    def como_dict(self):
        return {
            "restauradas": [
                {"caminho": c, "nome_antigo": a, "nome_novo": n, "usos": u} for c, a, n, u in self.restauradas
            ],
            "ja_restauradas": self.ja_restauradas,
            "nao_referenciados": self.nao_referenciados,
            "duvidas": [{"caminho": c, "motivo": m} for c, m in self.duvidas],
            "erros": [{"caminho": c, "mensagem": m} for c, m in self.erros],
        }


def _conferir_imagem(dados):
    from PIL import Image

    try:
        with Image.open(io.BytesIO(dados)) as imagem:
            imagem.verify()
    except Exception as erro:
        raise ArquivoRecusado("não é uma imagem que o site consiga mostrar") from erro


def _nome_restaurado(nome_antigo, sha256, extensao, limite):
    if "://" in nome_antigo:
        pasta, raiz, ext = identidade(nome_antigo)
        nome_antigo = f"{pasta}/{raiz}{ext}" if pasta else f"{raiz}{ext}"
    nome = PREFIXO + nome_antigo.lstrip("/")
    if len(nome) <= limite:
        return nome
    return nome_no_banco(nome_antigo, sha256, extensao, limite)


def restaurar_nome(nome_antigo, dados, refs, origem=""):
    """Guarda `dados` como "banco/<nome antigo>" e aponta para ele todo
    campo que usava o nome antigo. Devolve o nome novo."""
    if len(dados) > LIMITE_ARQUIVO:
        raise ArquivoRecusado("arquivo grande demais (máximo 60 MB)")
    if any(isinstance(ref.campo, models.ImageField) for ref in refs):
        _conferir_imagem(dados)
    preparados, tipo, extensao = preparar_arquivo(dados, origem or nome_antigo)
    sha256 = hashlib.sha256(preparados).hexdigest()
    limite = min((ref.campo.max_length or 100) for ref in refs) if refs else 255
    novo = _nome_restaurado(nome_antigo, sha256, extensao, limite)
    with transaction.atomic():
        if not guardar_bytes(novo, preparados, tipo):
            # Já existe "banco/<nome antigo>" com outro conteúdo: nome próprio.
            novo = nome_no_banco(nome_antigo, sha256, extensao, limite)
            if not guardar_bytes(novo, preparados, tipo):
                raise ArquivoRecusado("conflito de nome no banco")
        _repontar(refs, novo)
    return novo


def definir_foto(chave, pk, campo, dados, nome_do_arquivo):
    """Você escolhe a foto e diz de quem ela é: guarda `dados` e aponta o
    campo (ex.: core.Produtos #12, image_produto) para ela. Não depende do
    nome do arquivo. Devolve o objeto já com a foto nova."""
    alvo = next(
        ((m, c) for m, c in campos_de_arquivo() if m._meta.label == chave and c.name == campo),
        None,
    )
    if alvo is None:
        raise ArquivoRecusado("este campo não guarda foto")
    modelo, campo_de_arquivo = alvo
    try:
        objeto = modelo._base_manager.get(pk=pk)
    except (modelo.DoesNotExist, ValueError, TypeError) as erro:
        raise ArquivoRecusado("este cadastro não existe mais") from erro
    if not dados:
        raise ArquivoRecusado("o arquivo chegou vazio")
    if len(dados) > LIMITE_ARQUIVO:
        raise ArquivoRecusado("arquivo grande demais (máximo 60 MB)")
    if isinstance(campo_de_arquivo, models.ImageField):
        _conferir_imagem(dados)
    from django.core.files.base import ContentFile

    nome = posixpath.basename(str(nome_do_arquivo).replace("\\", "/")) or "foto"
    arquivo = getattr(objeto, campo_de_arquivo.name)
    arquivo.save(nome, ContentFile(dados), save=False)
    objeto.save(update_fields=[campo_de_arquivo.name])
    _limpar_cache_do_cardapio()
    return objeto


def _limpar_cache_do_cardapio():
    try:
        from .views import _limpar_cache_cardapio

        _limpar_cache_cardapio()
    except Exception:
        pass


def _indice_das_restauradas():
    """Índice dos nomes antigos que já voltaram para o banco ("banco/<antigo>")."""
    return Indice([n[len(PREFIXO):] for n in referencias(incluir_banco=True) if n.startswith(PREFIXO)])


def restaurar_lote(arquivos, simular=False):
    """`arquivos`: {caminho: função que devolve os bytes}. Associa o lote
    inteiro de uma vez (para pegar conflitos) e restaura o que tem certeza."""
    relatorio = Relatorio()
    refs = referencias()
    achados = associar(list(arquivos), refs, conteudo=lambda c: arquivos[c]())
    restauradas = None
    alterou = False
    for caminho, achado in achados.items():
        if not achado.nivel:
            if achado.motivo:
                relatorio.duvidas.append((caminho, achado.motivo))
                continue
            restauradas = restauradas or _indice_das_restauradas()
            if restauradas.procurar(caminho).nivel:
                relatorio.ja_restauradas.append(caminho)
            else:
                relatorio.nao_referenciados.append(caminho)
            continue
        try:
            dados = arquivos[caminho]()
            for nome_antigo in achado.nomes:
                usos = refs[nome_antigo]
                if simular:
                    if any(isinstance(r.campo, models.ImageField) for r in usos):
                        _conferir_imagem(dados)
                    novo = "(simulação)"
                else:
                    novo = restaurar_nome(nome_antigo, dados, usos, origem=caminho)
                    alterou = True
                relatorio.restauradas.append((caminho, nome_antigo, novo, len(usos)))
        except ArquivoRecusado as erro:
            relatorio.erros.append((caminho, str(erro)))
        except Exception as erro:
            logger.exception("Falha ao restaurar %s", caminho)
            relatorio.erros.append((caminho, f"falha ao guardar ({erro.__class__.__name__})"))
    if alterou:
        _limpar_cache_do_cardapio()
    return relatorio


def _ignorar(caminho):
    partes = caminho.replace("\\", "/").split("/")
    return any(p.startswith(".") or p == "__MACOSX" for p in partes) or caminho.endswith("/")


def restaurar_zip(origem, simular=False):
    """ZIP da Cloudinary (caminho ou arquivo aberto). Lê uma entrada por
    vez, sem carregar o ZIP inteiro na memória."""
    try:
        with zipfile.ZipFile(origem) as zip_:
            entradas = {}
            recusadas = Relatorio()
            for info in zip_.infolist():
                if info.is_dir() or _ignorar(info.filename):
                    continue
                if info.file_size > LIMITE_ARQUIVO:
                    recusadas.erros.append((info.filename, "arquivo grande demais (máximo 60 MB)"))
                    continue
                entradas[info.filename] = (lambda i=info: zip_.read(i))
            return restaurar_lote(entradas, simular=simular).juntar(recusadas)
    except zipfile.BadZipFile:
        relatorio = Relatorio()
        relatorio.erros.append((str(getattr(origem, "name", origem)), "ZIP inválido ou incompleto"))
        return relatorio


def restaurar_arquivo(caminho, dados, simular=False):
    """Uma foto solta (ou extraída do ZIP no navegador)."""
    return restaurar_lote({caminho: lambda: dados}, simular=simular)


def restaurar_caminhos(caminhos, simular=False):
    """Para o `manage.py restaurar_midia --zip`: ZIPs, fotos ou pastas."""
    relatorio = Relatorio()
    soltos = {}
    for bruto in caminhos:
        caminho = Path(bruto)
        if caminho.is_dir():
            for pasta, _, nomes in os.walk(caminho):
                for nome in nomes:
                    completo = Path(pasta) / nome
                    relativo = completo.relative_to(caminho).as_posix()
                    if not _ignorar(relativo):
                        soltos[relativo] = (lambda p=completo: p.read_bytes())
        elif zipfile.is_zipfile(caminho):
            relatorio.juntar(restaurar_zip(caminho, simular=simular))
        else:
            soltos[caminho.name] = (lambda p=caminho: p.read_bytes())
    if soltos:
        relatorio.juntar(restaurar_lote(soltos, simular=simular))
    return relatorio


def pastas_do_projeto():
    """Onde podem estar cópias das fotos dentro do projeto."""
    raizes = [Path(settings.MEDIA_ROOT), Path(settings.BASE_DIR), Path(settings.STATIC_ROOT)]
    vistas, unicas = set(), []
    for raiz in raizes:
        chave = str(raiz.resolve()) if raiz.exists() else str(raiz)
        if chave not in vistas:
            vistas.add(chave)
            unicas.append(raiz)
    return unicas


def copias_locais(refs=None):
    """{caminho relativo: leitor} dos arquivos nas pastas das fotos antigas
    (produtos/, imagens_categorias/... dentro de media/, do projeto e dos
    estáticos). Só as pastas que alguma referência usa."""
    refs = referencias() if refs is None else refs
    pastas = {identidade(nome)[0] for nome in refs}
    arquivos = {}
    for raiz in pastas_do_projeto():
        for pasta in sorted(pastas):
            for base in (raiz / pasta, raiz / "media" / pasta) if pasta else (raiz,):
                if not base.is_dir() or (not pasta and raiz == Path(settings.BASE_DIR)):
                    continue
                for item in sorted(base.iterdir()):
                    if item.is_file() and not item.name.startswith("."):
                        relativo = f"{item.relative_to(raiz).as_posix()}"
                        chave = relativo if relativo not in arquivos else f"{raiz.name}/{relativo}"
                        arquivos[chave] = (lambda p=item: p.read_bytes())
    return arquivos


def restaurar_copias_locais(simular=False):
    """Restaura o que tiver cópia confiável dentro do projeto. Idempotente;
    sem fotos antigas, não faz nada."""
    refs = referencias()
    if not refs:
        return Relatorio()
    return restaurar_lote(copias_locais(refs), simular=simular)


def restaurar_ao_migrar(sender=None, verbosity=1, **kwargs):
    """Depois do `migrate`: restaura sozinho as cópias locais. Nunca
    atrapalha o deploy (qualquer erro vira aviso)."""
    try:
        from django.core.files.storage import storages

        from .midia import MidiaNoBanco

        if not isinstance(storages["default"], MidiaNoBanco):
            return
        # Savepoint próprio: o migrate automático (core/auto_migracao.py)
        # roda dentro de uma transação; um erro aqui não pode desfazê-lo.
        with transaction.atomic():
            relatorio = restaurar_copias_locais()
        if verbosity and relatorio.restauradas:
            print(f"  Fotos restauradas das cópias do projeto: {len(relatorio.restauradas)}")
    except Exception:
        logger.warning("Restauração automática das fotos não rodou.", exc_info=True)


# ------------------------------------------------------------- Cloudinary

class CloudinaryDesativada(Exception):
    pass


def baixar_da_cloudinary(nome, legado, sessao=None):
    """Bytes do arquivo antigo pela URL pública. 401/"disabled" interrompe
    tudo (CloudinaryDesativada); 404 é FileNotFoundError."""
    import requests

    url = legado.url(nome)
    resposta = (sessao or requests).get(url, timeout=30)
    if resposta.status_code == 200:
        return resposta.content
    erro = resposta.headers.get("x-cld-error", "")
    texto = erro or resposta.text[:300]
    if resposta.status_code in (401, 403) or "disabled" in texto.lower():
        raise CloudinaryDesativada(texto or f"HTTP {resposta.status_code}")
    if resposta.status_code == 404:
        raise FileNotFoundError(nome)
    raise OSError(f"HTTP {resposta.status_code}: {texto}")


@dataclass
class ResultadoDoEspelho:
    copiadas: list = field(default_factory=list)   # (nome antigo, nome novo, usos)
    sumidas: list = field(default_factory=list)    # nomes que a Cloudinary não tem (404)
    erros: list = field(default_factory=list)      # (nome, mensagem)
    interrompido: str = ""                         # motivo da parada (conta desativada...)
    restantes: int = 0                             # ainda por copiar


def _repontar(refs, novo):
    for ref in refs:
        ref.modelo._base_manager.filter(pk=ref.pk, **{ref.campo.attname: ref.nome}).update(
            **{ref.campo.attname: novo}
        )


def espelhar(legado, limite=0, simular=False):
    """Copia da Cloudinary para o banco cada foto antiga ainda referenciada.

    Idempotente: o que já foi copiado não aparece mais como antigo. Na
    primeira resposta 401/"disabled" para tudo e conta o motivo.
    """
    import requests

    from .models import ArquivoNoBanco

    resultado = ResultadoDoEspelho()
    refs = referencias()
    pendentes = sorted(refs.items())
    sessao = requests.Session()
    feitos = 0
    alterou = False
    for nome, usos in pendentes:
        if limite and feitos >= limite:
            break
        feitos += 1
        ja_existe = PREFIXO + nome.lstrip("/")
        if "://" not in nome and ArquivoNoBanco.objects.filter(nome=ja_existe).exists():
            if not simular:
                with transaction.atomic():
                    _repontar(usos, ja_existe)
                alterou = True
            resultado.copiadas.append((nome, ja_existe, len(usos)))
            continue
        try:
            dados = baixar_da_cloudinary(nome, legado, sessao)
        except CloudinaryDesativada as erro:
            resultado.interrompido = str(erro)
            feitos -= 1
            break
        except FileNotFoundError:
            resultado.sumidas.append(nome)
            continue
        except Exception as erro:
            resultado.erros.append((nome, str(erro)[:200]))
            continue
        try:
            novo = "(simulação)" if simular else restaurar_nome(nome, dados, usos, origem=nome)
            alterou = alterou or not simular
            resultado.copiadas.append((nome, novo, len(usos)))
        except ArquivoRecusado as erro:
            resultado.erros.append((nome, str(erro)))
        except Exception as erro:
            logger.exception("Falha ao guardar %s", nome)
            resultado.erros.append((nome, f"falha ao guardar ({erro.__class__.__name__})"))
    resultado.restantes = len(pendentes) - feitos
    if alterou:
        _limpar_cache_do_cardapio()
    return resultado


def situacao_dos_caminhos(caminhos):
    """Antes de enviar (página "Restaurar fotos"): para cada caminho, se o
    arquivo restaura alguma foto, se fica na dúvida ou se não é usado."""
    refs = referencias()
    achados = associar(caminhos, refs)
    restauradas = None
    situacoes = {}
    for caminho, achado in achados.items():
        if achado.nivel:
            situacoes[caminho] = {"situacao": "restaurar", "nomes": list(achado.nomes)}
        elif achado.motivo:
            situacoes[caminho] = {"situacao": "duvida", "motivo": achado.motivo}
        else:
            restauradas = restauradas or _indice_das_restauradas()
            ja = restauradas.procurar(caminho).nivel
            situacoes[caminho] = {"situacao": "ja_restaurada" if ja else "sem_uso"}
    return situacoes
