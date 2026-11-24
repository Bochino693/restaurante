"""Telas e endereços das fotos: /midia/banco/<caminho> serve os arquivos
guardados no banco, e "Restaurar fotos" traz de volta as fotos antigas."""
import json
import logging
import zipfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files.storage import storages
from django.db import DatabaseError
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse, HttpResponseNotModified, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import parse_etags
from django.views import View
from django.views.decorators.http import require_safe

from . import midia, restauracao
from .models import ArquivoNoBanco

logger = logging.getLogger(__name__)

CACHE_ETERNO = "public, max-age=31536000, s-maxage=31536000, immutable"
CACHE_CURTO = "public, max-age=60, s-maxage=60"


def _cabecalhos(resposta, etag):
    resposta["ETag"] = etag
    resposta["Cache-Control"] = CACHE_ETERNO
    resposta["X-Content-Type-Options"] = "nosniff"
    # Aberto direto no navegador, um SVG/HTML enviado não roda script nenhum.
    resposta["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    return resposta


@require_safe
def servir_midia(request, caminho):
    """Entrega um arquivo do banco. O endereço nunca muda de conteúdo, então
    vai com cache de um ano (navegador e borda da Vercel); "?w=" entrega uma
    versão menor em WebP, só nas larguras da lista."""
    largura = None
    if "w" in request.GET:
        try:
            pedida = int(request.GET["w"])
        except (TypeError, ValueError):
            pedida = 0
        if pedida not in midia.LARGURAS:
            # Largura fora da lista: manda para a mais próxima (sem
            # redimensionar à toa uma versão nova a cada número).
            destino = request.path if pedida <= 0 else f"{request.path}?w={midia.largura_permitida(pedida)}"
            resposta = HttpResponsePermanentRedirect(destino)
            resposta["Cache-Control"] = CACHE_ETERNO
            return resposta
        largura = pedida

    nome = midia.PREFIXO + caminho
    dados_do_arquivo = midia.com_tabela(
        lambda: ArquivoNoBanco.objects.filter(nome=nome).values("sha256", "tipo").first(),
        savepoint=False,
    )
    if not dados_do_arquivo:
        resposta = HttpResponse("Arquivo não encontrado.", status=404, content_type="text/plain; charset=utf-8")
        resposta["Cache-Control"] = CACHE_CURTO
        return resposta

    sha256 = dados_do_arquivo["sha256"]
    etag = f'"{sha256}{f"-w{largura}" if largura else ""}"'
    pedidas = parse_etags(request.headers.get("If-None-Match", ""))
    if "*" in pedidas or etag in pedidas or etag.replace('"', 'W/"', 1) in pedidas:
        return _cabecalhos(HttpResponseNotModified(), etag)

    pronto = midia.ler_do_cache(sha256, largura)
    if pronto:
        conteudo, tipo = pronto
    else:
        bruto = ArquivoNoBanco.objects.filter(nome=nome).values_list("conteudo", flat=True).first()
        if bruto is None:  # apagado entre as duas consultas
            raise Http404
        conteudo, tipo = bytes(bruto), dados_do_arquivo["tipo"]
        if largura and tipo.startswith("image/"):
            reduzida = midia.miniatura(conteudo, largura)
            if reduzida:
                conteudo, tipo = reduzida, "image/webp"
        midia.gravar_no_cache(sha256, largura, conteudo, tipo)

    resposta = HttpResponse(conteudo, content_type=tipo or "application/octet-stream")
    resposta["Content-Length"] = str(len(conteudo))
    return _cabecalhos(resposta, etag)


# ------------------------------------------------------- Restaurar fotos

def _resumo(relatorio):
    partes = [f"{len(relatorio.restauradas)} foto(s) restaurada(s)"]
    if relatorio.ja_restauradas:
        partes.append(f"{len(relatorio.ja_restauradas)} já estava(m) no banco")
    if relatorio.nao_referenciados:
        partes.append(f"{len(relatorio.nao_referenciados)} sem uso no site")
    if relatorio.duvidas:
        partes.append(f"{len(relatorio.duvidas)} na dúvida (não usada(s))")
    if relatorio.erros:
        partes.append(f"{len(relatorio.erros)} com erro")
    return " · ".join(partes)


def _leitor(arquivo):
    def ler():
        arquivo.seek(0)
        return arquivo.read()
    return ler


class RestaurarFotosView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Só a equipe (staff/superusuário): mostra as fotos que faltam e recebe
    o ZIP da Cloudinary ou fotos soltas. Na Vercel cada requisição aceita
    ~4,5 MB, então a página abre o ZIP no navegador e envia uma foto por vez."""

    login_url = "login"

    def test_func(self):
        usuario = self.request.user
        return usuario.is_active and (usuario.is_staff or usuario.is_superuser)

    @staticmethod
    def _banco_ligado():
        return isinstance(storages["default"], midia.MidiaNoBanco)

    def _quer_json(self):
        return (
            self.request.headers.get("X-Requested-With") == "fetch"
            or "application/json" in self.request.headers.get("Accept", "")
        )

    def dispatch(self, request, *args, **kwargs):
        # A tabela das fotos é criada aqui se a migração não rodou no servidor.
        if request.user.is_authenticated and self.test_func():
            try:
                midia.garantir_tabela()
            except Exception:
                logger.exception("Não deu para conferir a tabela das fotos")
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def _faltando():
        return restauracao.descrever(restauracao.faltando())

    @staticmethod
    def _guardadas():
        total = ArquivoNoBanco.objects.aggregate(quantas=Count("id"), bytes=Sum("tamanho"))
        return {"quantas": total["quantas"] or 0, "mb": round((total["bytes"] or 0) / (1024 * 1024), 1)}

    def get(self, request):
        try:
            linhas = self._faltando()
            guardadas = self._guardadas()
            erro_do_banco = False
        except DatabaseError:
            logger.exception("Banco das fotos indisponível")
            linhas, guardadas, erro_do_banco = [], {"quantas": 0, "mb": 0}, True
        if request.GET.get("formato") == "json":
            return JsonResponse({"faltando": linhas, "guardadas": guardadas}, status=503 if erro_do_banco else 200)
        return render(request, "restaurar_fotos.html", {
            "faltando": linhas,
            "guardadas": guardadas,
            "erro_do_banco": erro_do_banco,
            "erro_do_teste": midia.testar_armazenamento() if self._banco_ligado() else "",
            "banco_ligado": self._banco_ligado(),
            "limite_envio": restauracao.limite_de_envio(),
            "limite_envio_mb": restauracao.limite_de_envio() // (1024 * 1024),
            "serverless": getattr(settings, "SERVERLESS", False),
        })

    def _erro(self, mensagem, status=400):
        if self._quer_json():
            return JsonResponse({"ok": False, "mensagem": mensagem}, status=status)
        messages.error(self.request, mensagem)
        return redirect("restaurar_fotos")

    def _definir(self, request):
        """Foto escolhida à mão para um produto/categoria (sem depender do nome)."""
        arquivo = request.FILES.get("arquivo")
        if not arquivo:
            return self._erro("Escolha a foto.")
        if arquivo.size > restauracao.limite_de_envio():
            return self._erro(f"Foto maior que {restauracao.limite_de_envio() // (1024 * 1024)} MB por envio.", 413)
        try:
            objeto = restauracao.definir_foto(
                request.POST.get("modelo", ""), request.POST.get("pk", ""), request.POST.get("campo", ""),
                arquivo.read(), arquivo.name,
            )
        except restauracao.ArquivoRecusado as erro:
            return self._erro(f"Não usei esta foto: {erro}.")
        except midia.ErroAoGuardarMidia as erro:
            return self._erro(str(erro), 503)
        campo = getattr(objeto, request.POST["campo"])
        mensagem = f"Foto de “{objeto}” guardada."
        if self._quer_json():
            return JsonResponse({
                "ok": True, "mensagem": mensagem, "nome": campo.name,
                "url": midia.url_da_foto(campo, 160), "faltando": len(restauracao.faltando()),
            })
        messages.success(request, mensagem)
        return redirect("restaurar_fotos")

    def post(self, request):
        if (request.GET.get("acao") or request.POST.get("acao")) == "definir":
            try:
                return self._definir(request)
            except Exception:
                logger.exception("Falha ao definir a foto")
                return self._erro("Não foi possível guardar a foto agora. Nada se perdeu: tente de novo em instantes.", 503)
        if not self._banco_ligado():
            return self._erro(
                "O armazenamento das fotos no banco está desligado neste ambiente "
                "(desenvolvimento). Nada foi alterado."
            )
        acao = request.GET.get("acao") or request.POST.get("acao") or "enviar"
        try:
            if acao == "verificar":
                return self._verificar(request)
            if acao == "locais":
                relatorio = restauracao.restaurar_copias_locais()
            elif acao == "enviar":
                relatorio = self._receber(request)
                if relatorio is None:
                    return self._erro("Escolha o ZIP baixado da Cloudinary ou as fotos.")
            else:
                return self._erro("Ação desconhecida.")
        except Exception:
            logger.exception("Falha ao restaurar fotos")
            return self._erro("Não foi possível restaurar agora. Nada se perdeu: tente de novo em instantes.", 503)

        if self._quer_json():
            return JsonResponse({
                "ok": True,
                "resumo": _resumo(relatorio),
                "relatorio": relatorio.como_dict(),
                "faltando": len(restauracao.faltando()),
            })
        (messages.success if relatorio.restauradas else messages.info)(request, _resumo(relatorio))
        for caminho, motivo in relatorio.duvidas[:20]:
            messages.warning(request, f"Na dúvida, não usei {caminho}: {motivo}.")
        for caminho, mensagem in relatorio.erros[:20]:
            messages.error(request, f"{caminho}: {mensagem}.")
        return redirect("restaurar_fotos")

    def _verificar(self, request):
        try:
            caminhos = json.loads(request.body or b"{}").get("caminhos", [])
        except (ValueError, AttributeError):
            caminhos = None
        if not isinstance(caminhos, list) or len(caminhos) > 2000:
            return self._erro("Lista de arquivos inválida (máximo 2000 por vez).")
        caminhos = [str(c)[:500] for c in caminhos]
        return JsonResponse({"ok": True, "situacoes": restauracao.situacao_dos_caminhos(caminhos)})

    def _receber(self, request):
        arquivos = request.FILES.getlist("arquivo")
        if not arquivos:
            return None
        limite = restauracao.limite_de_envio()
        relatorio = restauracao.Relatorio()
        soltos = {}
        for arquivo in arquivos:
            if arquivo.size > limite:
                relatorio.erros.append((arquivo.name, f"maior que {limite // (1024 * 1024)} MB por envio"))
                continue
            if zipfile.is_zipfile(arquivo):
                arquivo.seek(0)
                relatorio.juntar(restauracao.restaurar_zip(arquivo))
                continue
            caminho = request.POST.get("caminho") if len(arquivos) == 1 else ""
            soltos[caminho or arquivo.name] = _leitor(arquivo)
        if soltos:
            relatorio.juntar(restauracao.restaurar_lote(soltos))
        return relatorio
