from django.db import models


class Prime(models.Model):
    ativo = models.BooleanField(default=True)
    criacao = models.DateTimeField(auto_now_add=True, null=True)
    atualizado = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True


class CategoriaProdutos(Prime):
    imagem_categoria = models.ImageField(upload_to='imagens_categorias/')
    nome_categoria = models.CharField(max_length=150)

    def __str__(self):
        return self.nome_categoria

    class Meta:
        verbose_name = "Categoria de Produto"
        verbose_name_plural = "Categorias de Produto"


class Produtos(Prime):
    nome_produto = models.CharField(max_length=180, null=False)
    image_produto = models.ImageField(upload_to='produtos/')
    codigo = models.IntegerField(default=0)
    preco = models.DecimalField(decimal_places=2, max_digits=9)

    categoria = models.ForeignKey(
        CategoriaProdutos,
        on_delete=models.CASCADE,
        related_name="produtos"
    )

    def __str__(self):
        return self.nome_produto

    class Meta:
        verbose_name = "Produto"
        verbose_name_plural = "Produtos"


class Adicional(models.Model):
    nome = models.CharField(max_length=120)
    preco = models.DecimalField(max_digits=9, decimal_places=2)

    produtos = models.ManyToManyField(
        Produtos,
        related_name="adicionais_disponiveis"
    )

    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome

    class Meta:
        verbose_name = "Adicional"
        verbose_name_plural = "Adicionais"


class PratoDoDia(models.Model):
    DIAS_SEMANA = (
        (0, "Segunda-feira"),
        (1, "Terça-feira"),
        (2, "Quarta-feira"),
        (3, "Quinta-feira"),
        (4, "Sexta-feira"),
        (5, "Sábado"),
        (6, "Domingo"),
    )

    produto = models.ForeignKey(
        "Produtos",
        on_delete=models.CASCADE,
        related_name="agenda_prato_dia"
    )

    dia_semana = models.IntegerField(
        choices=DIAS_SEMANA
    )

    ativo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.get_dia_semana_display()} - {self.produto.nome_produto}"


class EstoqueProdutos(Prime):
    produtos = models.ForeignKey(Produtos, on_delete=models.CASCADE, related_name='produto_estoque')
    data_validade = models.DateField(verbose_name="Data de Validade")
    quantidade = models.IntegerField(default=0)
    preco_fornecedor = models.DecimalField(decimal_places=2, max_digits=9)
    lote = models.IntegerField(default=1)

    class Meta:
        verbose_name = "Estoque de Produtos"
        verbose_name_plural = "Estoques de Produtos"


class ItensPedido(Prime):
    quantidade = models.IntegerField(default=1)
    preco_unitario = models.DecimalField(decimal_places=2, max_digits=9)
    subtotal = models.DecimalField(decimal_places=2, max_digits=9)

    produto = models.ForeignKey(
        Produtos,
        on_delete=models.SET_NULL,
        null=True,
        related_name='itens_pedido'
    )

    # Este campo salvará os adicionais selecionados no PDV
    # Ex: [{"nome": "Ovo Extra", "preco": "2.00"}]
    adicionais = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.produto.nome_produto

    class Meta:
        verbose_name = "Item de Pedido"
        verbose_name_plural = "Itens de Pedido"

    def save(self, *args, **kwargs):
        # Opcional: Você pode recalcular o subtotal aqui no backend para segurança
        super().save(*args, **kwargs)


class Pedidos(Prime):
    # Definindo as opções de status
    class StatusPedido(models.TextChoices):
        PAGO = 'PAGO', 'Pago'
        PREPARO = 'PREPARO', 'Em Preparo'
        ENTREGA = 'ENTREGA', 'Em Entrega'
        FINALIZADO = 'FINALIZADO', 'Finalizado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    itens = models.ManyToManyField(ItensPedido)

    # Novos campos de Status e Dados do Cliente
    status = models.CharField(
        max_length=20,
        choices=StatusPedido.choices,
        default=StatusPedido.PAGO
    )

    nome_cliente = models.CharField(max_length=90, blank=True, null=True)
    impresso = models.BooleanField(default=False)

    entrega = models.BooleanField(default=False)
    cpf = models.CharField(max_length=14, blank=True, null=True)

    rua = models.CharField(max_length=120, null=True, blank=True)
    numero = models.CharField(max_length=50, null=True, blank=True)

    cep = models.CharField(max_length=9, blank=True, null=True)

    total = models.DecimalField(
        decimal_places=2,
        max_digits=9,
        default=0
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"

    def __str__(self):
        return f"Pedido #{self.id} - {self.status}"


class ProdutosMaisClick(Prime):
    produto = models.ForeignKey(Produtos, related_name='clicks_produtos', on_delete=models.CASCADE)
    quantidade = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Produto mais clicado"
        verbose_name_plural = "Produtos mais clicados"


class PratoDiaMaisClick(Prime):
    prato = models.ForeignKey(PratoDoDia, related_name='clicks_pratos', on_delete=models.CASCADE)
    quantidade = models.IntegerField(default=0)

    class Meta:
        verbose_name = "Prato do dia mais clicado"
        verbose_name_plural = "Pratos do dia mais clicados"
