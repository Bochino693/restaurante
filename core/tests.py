from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .horario import loja_aberta, status_da_loja
from .models import CategoriaProdutos, ItensPedido, Pedidos, Produtos, ProdutosMaisClick
from .views import _reais

SP = ZoneInfo("America/Sao_Paulo")


def quando(ano, mes, dia, hora, minuto=0):
    return datetime(ano, mes, dia, hora, minuto, tzinfo=SP)


class HorarioTests(TestCase):
    """Segunda a sexta, das 11h às 16h."""

    def test_aberta_em_dia_util_no_horario(self):
        self.assertTrue(loja_aberta(quando(2026, 9, 28, 12)))   # segunda
        self.assertTrue(loja_aberta(quando(2026, 10, 2, 15, 59)))  # sexta

    def test_fechada_no_sabado_e_domingo(self):
        self.assertFalse(loja_aberta(quando(2026, 10, 3, 12)))  # sábado
        self.assertFalse(loja_aberta(quando(2026, 10, 4, 12)))  # domingo

    def test_fechada_fora_do_horario(self):
        self.assertFalse(loja_aberta(quando(2026, 9, 28, 10, 59)))
        self.assertFalse(loja_aberta(quando(2026, 9, 28, 16)))

    def test_proxima_abertura_na_sexta_a_noite_e_segunda(self):
        status = status_da_loja(quando(2026, 10, 2, 18))
        self.assertFalse(status["aberta"])
        self.assertEqual(status["detalhe"], "abre segunda às 11h")

    def test_proxima_abertura_de_manha_e_hoje(self):
        self.assertEqual(status_da_loja(quando(2026, 9, 29, 9))["detalhe"], "abre hoje às 11h")


class TelasTests(TestCase):
    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        categoria = CategoriaProdutos(nome_categoria="Marmitas")
        categoria.imagem_categoria.name = "x.png"
        categoria.save()
        # Produto sem foto: antes derrubava o cardápio inteiro (erro 500).
        self.produto = Produtos.objects.create(
            nome_produto="Marmita d'Avó", categoria=categoria, preco=Decimal("22.90"), codigo=10
        )
        item = ItensPedido.objects.create(
            produto=self.produto, quantidade=2, preco_unitario=Decimal("22.90"), subtotal=Decimal("45.80")
        )
        self.pedido = Pedidos.objects.create(
            nome_cliente="Ana", total=Decimal("45.80"), forma_pagamento="DINHEIRO",
            status=Pedidos.StatusPedido.ENTREGA,
        )
        self.pedido.itens.add(item)

    def test_cardapio_publico_sem_login_e_com_produto_sem_foto(self):
        resposta = self.client.get(reverse("cardapio"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Marmita d&#x27;Avó")
        self.assertContains(resposta, "catalogo-json")

    def test_dashboard_renderiza_e_responde_json(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboards")).status_code, 200)
        dados = self.client.get(reverse("dashboards") + "?atalho=tudo&ajax=1").json()
        self.assertEqual(dados["total_pedidos"], 1)
        self.assertIn("grafico_dia_semana", dados)
        self.assertIn("grafico_categorias", dados)

    def test_avancar_status_exige_login(self):
        url = reverse("avancar_status", args=[self.pedido.id])
        self.client.post(url)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, Pedidos.StatusPedido.ENTREGA)

    def test_finalizar_em_dinheiro_exige_confirmacao(self):
        self.client.force_login(self.usuario)
        url = reverse("avancar_status", args=[self.pedido.id])
        cabecalho = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        self.assertEqual(self.client.post(url, {}, **cabecalho).status_code, 400)
        self.assertEqual(self.client.post(url, {"confirmacao": "confirmar"}, **cabecalho).status_code, 200)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.status, Pedidos.StatusPedido.FINALIZADO)

    def test_clique_do_cardapio_soma_no_dia(self):
        url = reverse("registrar_clique", args=[self.produto.id])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(ProdutosMaisClick.objects.get(produto=self.produto).quantidade, 2)

    def test_vendas_formata_dinheiro(self):
        self.assertEqual(_reais(Decimal("53140.3000000000")), "53.140,30")
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("vendas")).status_code, 200)

    @override_settings(IMPRESSAO_API_KEY="chave-de-teste")
    def test_api_da_impressora_exige_a_chave(self):
        self.assertEqual(self.client.get("/api/pedidos-impressao/").status_code, 401)
        resposta = self.client.get("/api/pedidos-impressao/", HTTP_X_API_KEY="chave-de-teste")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["pedidos"][0]["id"], self.pedido.id)

    def test_telas_do_painel_abrem(self):
        self.client.force_login(self.usuario)
        for nome in ["pedidos", "caixa", "estoque", "gerenciar_pratos_dia"]:
            with self.subTest(tela=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)
