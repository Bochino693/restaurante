from django.contrib import admin
from django.utils.html import format_html
from .models import CategoriaProdutos, Produtos, EstoqueProdutos, ItensPedido, Pedidos, Adicional, PratoDoDia


# Configurações de exibição de imagem miniatura
def exibir_miniatura(obj, campo_imagem):
    imagem = getattr(obj, campo_imagem)
    if imagem:
        return format_html('<img src="{}" style="width: 45px; height: 45px; border-radius: 8px; object-fit: cover;" />',
                           imagem.url)
    return "Sem imagem"


@admin.register(CategoriaProdutos)
class CategoriaProdutosAdmin(admin.ModelAdmin):
    list_display = ('get_image', 'nome_categoria', 'ativo', 'criacao')
    list_editable = ('ativo',)
    search_fields = ('nome_categoria',)

    def get_image(self, obj):
        return exibir_miniatura(obj, 'imagem_categoria')

    get_image.short_description = 'Imagem'


@admin.register(Produtos)
class ProdutosAdmin(admin.ModelAdmin):
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