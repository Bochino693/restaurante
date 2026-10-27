"""Certificado e assinatura do QZ Tray (gratuito, certificado próprio).

O QZ Tray só "lembra" a permissão de um site quando a página se identifica
com um certificado e assina cada requisição. Aqui o servidor entrega o
certificado e assina com a chave privada, que NUNCA vai para o navegador
nem para o GitHub (fica na variável de ambiente QZ_CHAVE_PRIVADA).
"""
import base64
import logging
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

_PASTA_LOCAL = Path(settings.BASE_DIR) / "qz"


def _texto(valor, arquivo):
    """Variável de ambiente (aceita \\n literal) ou arquivo local de desenvolvimento."""
    if valor:
        return valor.replace("\\n", "\n").strip() + "\n"
    caminho = _PASTA_LOCAL / arquivo
    if caminho.exists():
        return caminho.read_text(encoding="utf-8").strip() + "\n"
    return ""


def certificado_pem():
    return _texto(getattr(settings, "QZ_CERTIFICADO", ""), "digital-certificate.txt")


def _chave_privada():
    pem = _texto(getattr(settings, "QZ_CHAVE_PRIVADA", ""), "private-key.pem")
    if not pem:
        return None
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _nao_autenticado(request):
    return not (getattr(request, "user", None) and request.user.is_authenticated)


@require_GET
@ensure_csrf_cookie
def qz_certificado(request):
    if _nao_autenticado(request):
        return HttpResponse("Não autorizado", status=401, content_type="text/plain")
    pem = certificado_pem()
    if not pem:
        return HttpResponse("Certificado do QZ não configurado.", status=503, content_type="text/plain")
    resp = HttpResponse(pem, content_type="text/plain; charset=utf-8")
    resp["Cache-Control"] = "private, max-age=3600"
    return resp


@require_POST
@csrf_protect
def qz_assinar(request):
    """Assina (SHA512 + RSA) o texto que o QZ Tray pede para autorizar."""
    if _nao_autenticado(request):
        return HttpResponse("Não autorizado", status=401, content_type="text/plain")
    try:
        chave = _chave_privada()
    except Exception:
        logger.exception("Chave privada do QZ inválida")
        chave = None
    if chave is None:
        return HttpResponse("Chave do QZ não configurada.", status=503, content_type="text/plain")

    dados = request.body
    if not dados or len(dados) > 20_000:
        return HttpResponse("Requisição inválida.", status=400, content_type="text/plain")

    assinatura = chave.sign(dados, padding.PKCS1v15(), hashes.SHA512())
    resp = HttpResponse(base64.b64encode(assinatura).decode(), content_type="text/plain")
    resp["Cache-Control"] = "no-store"
    return resp
