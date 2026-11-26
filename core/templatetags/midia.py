from django import template

from core.midia import PREFIXO, url_da_foto

register = template.Library()


@register.filter
def foto(campo, largura=None):
    """{{ produto.image_produto|foto:480 }}: endereço da foto; as do banco já
    vêm na largura pedida ("?w=", WebP). Sem foto ou sem endereço: ""."""
    return url_da_foto(campo, largura)


@register.filter
def foto_tamanhos(campo, larguras):
    """{{ produto.image_produto|foto_tamanhos:"240,320,480" }}: o srcset
    para o navegador baixar só o tamanho que a tela pede. Só para as fotos
    do banco (as antigas têm um endereço só): senão, ""."""
    if not campo or not str(getattr(campo, "name", "")).startswith(PREFIXO):
        return ""
    partes = []
    for largura in str(larguras).split(","):
        url = url_da_foto(campo, int(largura))
        if url:
            partes.append(f"{url} {int(largura)}w")
    return ", ".join(partes)
