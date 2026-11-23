from django import template

from core.midia import url_da_foto

register = template.Library()


@register.filter
def foto(campo, largura=None):
    """{{ produto.image_produto|foto:480 }}: endereço da foto; as do banco já
    vêm na largura pedida ("?w=", WebP). Sem foto ou sem endereço: ""."""
    return url_da_foto(campo, largura)
