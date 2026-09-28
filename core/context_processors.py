import hashlib
from pathlib import Path

from django.utils import timezone

from .models import Pedidos
from .horario import status_da_loja

_PASTA_ESTATICOS = Path(__file__).resolve().parent / "static" / "core"


def _versao_dos_estaticos():
    """Impressão digital dos CSS/JS/imagens do sistema.

    Entra como "?v=" nos links: arquivo mudou, endereço muda, e o navegador
    (e a borda da Vercel) buscam o novo. Enquanto não muda, fica em cache.
    Calculada uma vez, quando a função sobe.
    """
    h = hashlib.sha1()
    for arquivo in sorted(_PASTA_ESTATICOS.rglob("*")):
        if arquivo.is_file():
            h.update(arquivo.name.encode())
            h.update(arquivo.read_bytes())
    return h.hexdigest()[:10]


VERSAO_ESTATICOS = _versao_dos_estaticos()

STATUS_EM_ANDAMENTO = (
    Pedidos.StatusPedido.PAGO,
    Pedidos.StatusPedido.PREPARO,
    Pedidos.StatusPedido.ENTREGA,
)


def global_context(request):
    contexto = {"versao_estaticos": VERSAO_ESTATICOS}

    # O cardápio público não mostra o sino: nada de consulta ao banco para
    # quem só veio ver o cardápio.
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return contexto

    # Pendentes = pedidos de HOJE ainda em andamento (antes contava todos os
    # pedidos já feitos, e o número só crescia).
    inicio = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    contexto["pedidos_pendentes_count"] = Pedidos.objects.filter(
        criado_em__gte=inicio,
        status__in=STATUS_EM_ANDAMENTO,
    ).count()
    contexto["loja"] = status_da_loja()
    return contexto
