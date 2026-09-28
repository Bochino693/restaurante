"""O horário de funcionamento, num lugar só.

Segunda a sexta, das LOJA_ABRE às LOJA_FECHA (settings). Antes a regra
estava repetida no Python (que aceitava sábado) e em textos soltos no
cardápio ("segunda a sábado", "Abre Seg às 11h") que discordavam entre si.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

NOMES_DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _dias():
    return tuple(getattr(settings, "LOJA_DIAS", (0, 1, 2, 3, 4)))


def loja_aberta(agora=None):
    agora = agora or timezone.localtime()
    return (
        agora.weekday() in _dias()
        and settings.LOJA_ABRE <= agora.hour < settings.LOJA_FECHA
    )


def proxima_abertura(agora=None):
    """Quando a loja abre de novo, a partir de `agora` (se já estiver aberta,
    devolve a abertura de hoje)."""
    agora = agora or timezone.localtime()
    dia = agora.replace(hour=settings.LOJA_ABRE, minute=0, second=0, microsecond=0)
    if loja_aberta(agora):
        return dia
    if agora.hour >= settings.LOJA_ABRE or agora.weekday() not in _dias():
        dia += timedelta(days=1)
    while dia.weekday() not in _dias():
        dia += timedelta(days=1)
    return dia


def texto_dias():
    dias = _dias()
    if dias == (0, 1, 2, 3, 4):
        return "segunda a sexta"
    return ", ".join(NOMES_DIAS[d] for d in dias)


def status_da_loja(agora=None):
    """Tudo o que a tela precisa dizer sobre o horário."""
    agora = agora or timezone.localtime()
    aberta = loja_aberta(agora)
    horario = f"{texto_dias()}, das {settings.LOJA_ABRE}h às {settings.LOJA_FECHA}h"
    if aberta:
        detalhe = f"até {settings.LOJA_FECHA}h"
    else:
        abre = proxima_abertura(agora)
        if abre.date() == agora.date():
            quando = "hoje"
        elif abre.date() == (agora + timedelta(days=1)).date():
            quando = "amanhã"
        else:
            quando = NOMES_DIAS[abre.weekday()]
        detalhe = f"abre {quando} às {settings.LOJA_ABRE}h"
    return {
        "aberta": aberta,
        "detalhe": detalhe,
        "horario": horario,
        "abre": settings.LOJA_ABRE,
        "fecha": settings.LOJA_FECHA,
        "dias": list(_dias()),
    }
