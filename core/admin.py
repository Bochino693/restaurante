import logging

from django import forms
from django.contrib import admin, messages
from django.db import models
from django.http import HttpResponseRedirect
from django.utils.html import format_html

from .midia import ErroAoGuardarMidia, url_da_foto
from .models import CategoriaProdutos, Produtos, EstoqueProdutos, ItensPedido, Pedidos, Adicional, PratoDoDia

logger = logging.getLogger(__name__)


# Configurações de exibição de imagem miniatura
def exibir_miniatura(obj, campo_imagem):
    imagem = getattr(obj, campo_imagem)
    url = url_da_foto(imagem, 96)
    if url:
        return format_html('<img src="{}" alt="" style="width: 45px; height: 45px; border-radius: 8px; object-fit: cover;" />',
                           url)
    return "Sem imagem"


class FormularioComFotos(forms.ModelForm):
    """Guarda a foto enviada ANTES de salvar o resto: se o armazenamento
    falhar, o formulário volta com o aviso no campo da foto (e tudo o que
    foi digitado), em vez de uma página de erro 500."""

    def _post_clean(self):
        super()._post_clean()
        if self.errors:
            return
        for campo in self.instance._meta.concrete_fields:
            if not isinstance(campo, models.FileField) or campo.name not in self.fields:
                continue
            arquivo = getattr(self.instance, campo.attname)
            if not arquivo or getattr(arquivo, "_committed", True):
                continue
            try:
                arquivo.save(arquivo.name, arquivo.file, save=False)
            except Exception as erro:
                logger.exception("Falha ao guardar a foto de %s", self.instance._meta.label)
                mensagem = str(erro) if isinstance(erro, ErroAoGuardarMidia) else ErroAoGuardarMidia.MENSAGEM
                self.add_error(campo.name, mensagem)


class AdminComFotos(admin.ModelAdmin):
    form = FormularioComFotos

    class Media:
        # Foto que não carrega vira o quadro "Foto em atualização".
        js = ("core/js/fotos.js",)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        # Rede de segurança: qualquer falha do armazenamento vira aviso.
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except ErroAoGuardarMidia as erro:
            self.message_user(request, str(erro), messages.ERROR)
            return HttpResponseRedirect(request.get_full_path())


@admin.register(CategoriaProdutos)
class CategoriaProdutosAdmin(AdminComFotos):
    list_display = ('get_image', 'nome_categoria', 'ativo', 'criacao')
    list_editable = ('ativo',)
    search_fields = ('nome_categoria',)

    def get_image(self, obj):
        return exibir_miniatura(obj, 'imagem_categoria')

    get_image.short_description = 'Imagem'


@admin.register(Produtos)
class ProdutosAdmin(AdminComFotos):
    list_display = ('get_image', 'nome_produto', 'codigo', 'categoria', 'preco', 'ativo')
    list_filter = ('categoria', 'ativo')
    search_fields = ('nome_produto', 'codigo')
    list_editable = ('preco', 'ativo')
    list_per_page = 20

    def get_image(self, obj):
        return exibir_miniatura(obj, 'image_produto')

    get_image.short_description = 'Foto'


@admin.register(EstoqueProdutos)
class EstoqueProdutosAdmin(admin.ModelAdmin):
    list_display = ('produtos', 'quantidade', 'data_validade', 'preco_fornecedor', 'lote', 'status_estoque')
    list_filter = ('data_validade', 'produtos__categoria')
    search_fields = ('produtos__nome_produto', 'lote')

    def status_estoque(self, obj):
        if obj.quantidade <= 5:
            return format_html('<span style="color: red; font-weight: bold;">Crítico</span>')
        return format_html('<span style="color: green;">OK</span>')

    status_estoque.short_description = 'Status'


class ItensPedidoInline(admin.TabularInline):
    model = Pedidos.itens.through  # Como é ManyToMany
    extra = 0
    readonly_fields = ('get_subtotal',)

    def get_subtotal(self, obj):
        # Acesso ao item através da tabela intermediária
        item = obj.itenspedido
        return f"R$ {item.subtotal}"

    get_subtotal.short_description = "Subtotal"


@admin.register(ItensPedido)
class ItensPedidoAdmin(admin.ModelAdmin):
    list_display = ('produto', 'quantidade', 'preco_unitario', 'subtotal')
    readonly_fields = ('subtotal',)


@admin.register(Pedidos)
class PedidosAdmin(admin.ModelAdmin):
    list_display = ('id', 'total', 'criado_em', 'quantidade_itens')
    list_filter = ('criado_em',)
    date_hierarchy = 'criado_em'

    # inlines = [ItensPedidoInline] # Opcional: ver itens dentro do pedido

    def quantidade_itens(self, obj):
        return obj.itens.count()

    quantidade_itens.short_description = 'Qtd Itens'


@admin.register(Adicional)
class AdicionalAdmin(admin.ModelAdmin):

    list_display = (
        "nome",
        "preco",
        "ativo",
    )

    list_filter = (
        "ativo",
    )

    search_fields = (
        "nome",
    )

    filter_horizontal = (
        "produtos",
    )


@admin.register(PratoDoDia)
class PratoDoDiaAdmin(admin.ModelAdmin):

    list_display = (
        "produto",
        "dia_semana",
        "ativo",
    )

    list_filter = (
        "dia_semana",
        "ativo",
    )

    search_fields = (
        "produto__nome_produto",
    )

    ordering = (
        "dia_semana",
    )