"""Certificado e assinatura do QZ Tray (gratuito, certificado próprio).

O QZ Tray só "lembra" a permissão de um site quando a página se identifica
com um certificado e assina cada requisição. Aqui o servidor entrega o
certificado e assina com a chave privada, que NUNCA vai para o navegador
nem para o GitHub (fica na variável de ambiente QZ_CHAVE_PRIVADA).

- /api/qz/certificado/   público (um certificado é público por natureza):
                         é ele que vai para o QZ do caixa como override.crt
                         (?baixar=1 baixa o arquivo pronto).
- /api/qz/assinar/       com login: assina o que o QZ pede.
- /api/qz/diagnostico/   com login: diz se as variáveis estão certas e se
                         a chave e o certificado formam um par.
"""
import base64
import datetime
import logging
import re
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

_PASTA_LOCAL = Path(settings.BASE_DIR) / "qz"


def _limpar_pem(valor):
    """Aceita o valor do jeito que costuma ser colado na Vercel: com \\n
    literal, entre aspas ou com o nome da variável na frente."""
    texto = str(valor or "").strip()
    texto = re.sub(r"^\s*QZ_[A-Z_]+\s*=\s*", "", texto)          # "QZ_CERTIFICADO=..."
    if len(texto) >= 2 and texto[0] == texto[-1] and texto[0] in "\"'":
        texto = texto[1:-1].strip()
    texto = texto.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    return texto.strip() + "\n" if texto.strip() else ""


def _texto(valor, arquivo):
    """Variável de ambiente ou, no desenvolvimento, o arquivo da pasta qz/.
    Devolve (texto, origem)."""
    if valor and str(valor).strip():
        return _limpar_pem(valor), "variavel"
    caminho = _PASTA_LOCAL / arquivo
    if caminho.exists():
        return _limpar_pem(caminho.read_text(encoding="utf-8")), "arquivo"
    return "", "nenhuma"


def certificado_pem():
    return _texto(getattr(settings, "QZ_CERTIFICADO", ""), "digital-certificate.txt")[0]


def _chave_privada():
    pem = _texto(getattr(settings, "QZ_CHAVE_PRIVADA", ""), "private-key.pem")[0]
    if not pem:
        return None
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _assinar(chave, dados):
    return chave.sign(dados, padding.PKCS1v15(), hashes.SHA512())


def _impressao_digital(cert, algoritmo):
    return ":".join(f"{b:02X}" for b in cert.fingerprint(algoritmo))


def estado():
    """Tudo o que o diagnóstico da impressora precisa saber do servidor."""
    cert_pem, origem_cert = _texto(getattr(settings, "QZ_CERTIFICADO", ""), "digital-certificate.txt")
    chave_pem, origem_chave = _texto(getattr(settings, "QZ_CHAVE_PRIVADA", ""), "private-key.pem")
    info = {
        "certificado": "ausente", "chave": "ausente", "par_confere": None,
        "origem_certificado": origem_cert, "origem_chave": origem_chave,
        "titular": "", "valido_ate": "", "vencido": None, "sha1": "", "sha256": "",
        "problemas": [],
    }

    cert = None
    if cert_pem:
        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode())
            info["certificado"] = "ok"
            nomes = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
            info["titular"] = nomes[0].value if nomes else ""
            fim = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
            info["valido_ate"] = fim.date().isoformat()
            info["vencido"] = fim < datetime.datetime.now(datetime.timezone.utc)
            info["sha1"] = _impressao_digital(cert, hashes.SHA1())
            info["sha256"] = _impressao_digital(cert, hashes.SHA256())
            if info["vencido"]:
                info["problemas"].append("O certificado venceu. Gere um novo (qz/gerar_certificado.py).")
        except Exception:
            info["certificado"] = "invalido"
            info["problemas"].append(
                "QZ_CERTIFICADO não é um certificado válido. Cole de novo a linha inteira do "
                "COLAR-NA-VERCEL.txt, sem aspas, começando em -----BEGIN CERTIFICATE-----."
            )
    else:
        info["problemas"].append(
            "QZ_CERTIFICADO não está configurado neste deploy. Crie a variável na Vercel "
            "(Production) e faça um novo deploy."
        )

    chave = None
    if chave_pem:
        try:
            chave = serialization.load_pem_private_key(chave_pem.encode(), password=None)
            info["chave"] = "ok"
        except Exception:
            info["chave"] = "invalida"
            info["problemas"].append(
                "QZ_CHAVE_PRIVADA não é uma chave válida. Cole de novo a linha inteira do "
                "COLAR-NA-VERCEL.txt, sem aspas, começando em -----BEGIN PRIVATE KEY-----."
            )
    else:
        info["problemas"].append(
            "QZ_CHAVE_PRIVADA não está configurada neste deploy. Crie a variável na Vercel "
            "(Production) e faça um novo deploy."
        )

    if cert is not None and chave is not None:
        teste = b"diagnostico-qz"
        try:
            cert.public_key().verify(_assinar(chave, teste), teste, padding.PKCS1v15(), hashes.SHA512())
            info["par_confere"] = True
        except Exception:
            info["par_confere"] = False
            info["problemas"].append(
                "A chave privada NÃO é a do certificado (foram gerados em vezes diferentes). "
                "Use as duas linhas do MESMO COLAR-NA-VERCEL.txt, ou gere um par novo e "
                "configure as duas variáveis de novo."
            )
    return info


def _nao_autenticado(request):
    return not (getattr(request, "user", None) and request.user.is_authenticated)


@require_GET
@ensure_csrf_cookie
def qz_certificado(request):
    pem = certificado_pem()
    if not pem:
        return HttpResponse("Certificado do QZ não configurado.", status=503, content_type="text/plain")
    resp = HttpResponse(pem, content_type="text/plain; charset=utf-8")
    if request.GET.get("baixar"):
        # O arquivo que vai para C:\Program Files\QZ Tray\override.crt
        resp["Content-Disposition"] = 'attachment; filename="override.crt"'
    resp["Cache-Control"] = "no-cache"
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

    resp = HttpResponse(base64.b64encode(_assinar(chave, dados)).decode(), content_type="text/plain")
    resp["Cache-Control"] = "no-store"
    return resp


@require_GET
def qz_diagnostico(request):
    if _nao_autenticado(request):
        return JsonResponse({"erro": "Não autorizado"}, status=401)
    resp = JsonResponse(estado())
    resp["Cache-Control"] = "no-store"
    return resp
