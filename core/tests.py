import re
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


class RelatorioBebidasTests(TestCase):
    """Bebidas = produtos de categoria com "bebida", "refri", "suco"... no nome."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        marmitas = self._categoria("Marmitas")
        bebidas = self._categoria("Bebidas")
        sucos = self._categoria("SUCOS NATURAIS")
        marmita = Produtos.objects.create(nome_produto="Marmita", categoria=marmitas, preco=Decimal("20.00"))
        coca = Produtos.objects.create(nome_produto="Coca-Cola 2L", categoria=bebidas, preco=Decimal("6.00"))
        suco = Produtos.objects.create(nome_produto="Suco de laranja", categoria=sucos, preco=Decimal("5.00"))

        # Pix com entrega: marmita + 2 cocas + 1 suco (duas bebidas no mesmo pedido).
        self.pedido_pix = self._pedido(
            "PIX", [(marmita, 1), (coca, 2), (suco, 1)], taxa=Decimal("5.00"), entrega=True
        )
        Pedidos.objects.filter(pk=self.pedido_pix.pk).update(
            rua="Rua das Flores", numero="12", cep="01234-000", telefone="(11) 98765-4321"
        )
        # Dinheiro: só a marmita.
        self.pedido_dinheiro = self._pedido("DINHEIRO", [(marmita, 1)])
        # Cancelado: não entra em relatório nenhum.
        self._pedido("PIX", [(coca, 3)], status=Pedidos.StatusPedido.CANCELADO)

    def _categoria(self, nome):
        categoria = CategoriaProdutos(nome_categoria=nome)
        categoria.imagem_categoria.name = "x.png"
        categoria.save()
        return categoria

    def _pedido(self, pagamento, itens, taxa=Decimal("0.00"), entrega=False, status=Pedidos.StatusPedido.PAGO):
        criados = [
            ItensPedido.objects.create(
                produto=produto, quantidade=qtd, preco_unitario=produto.preco, subtotal=produto.preco * qtd
            )
            for produto, qtd in itens
        ]
        pedido = Pedidos.objects.create(
            nome_cliente="Cliente", forma_pagamento=pagamento, status=status, entrega=entrega,
            taxa_motoca=taxa, total=sum((i.subtotal for i in criados), Decimal("0.00")),
        )
        pedido.itens.add(*criados)
        return pedido

    def test_relatorio_da_tela_de_pedidos(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("pedidos") + "?periodo=hoje")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["total_bebidas"], "17,00")
        self.assertEqual(resposta.context["qtd_bebidas"], 3)
        self.assertEqual(
            resposta.context["bebidas_vendidas"],
            [
                {"nome": "Coca-Cola 2L", "unidades": 2, "receita": "12,00"},
                {"nome": "Suco de laranja", "unidades": 1, "receita": "5,00"},
            ],
        )
        # O resto do relatório continua igual.
        self.assertEqual(resposta.context["total_pix"], "37,00")
        self.assertEqual(resposta.context["total_taxa_motoca"], "5,00")
        self.assertContains(resposta, 'data-relatorio="bebidas"')

    def test_bebidas_obedecem_ao_filtro_de_pagamento(self):
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("resumo_pedidos") + "?periodo=hoje&pagamento=DINHEIRO").json()
        self.assertEqual(dados["total_bebidas"], "0,00")
        self.assertEqual(dados["qtd_bebidas"], 0)
        dados = self.client.get(reverse("resumo_pedidos") + "?periodo=hoje&pagamento=PIX").json()
        self.assertEqual(dados["total_bebidas"], "17,00")
        self.assertEqual(dados["total_pix"], "37,00")

    def test_relatorio_grafico_de_bebidas_nas_estatisticas(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboards") + "?atalho=hoje").status_code, 200)
        dados = self.client.get(reverse("dashboards") + "?atalho=hoje&ajax=1").json()

        bebidas = dados["bebidas"]
        self.assertEqual(bebidas["receita"], "17,00")
        self.assertEqual(bebidas["unidades"], 3)
        self.assertEqual(bebidas["lider"], "Coca-Cola 2L")
        self.assertEqual(bebidas["participacao"], 29.8)       # 17 de 57 em itens
        self.assertEqual(bebidas["pedidos_com"], 1)            # sem contar duas vezes
        self.assertEqual(bebidas["pedidos_pct"], 50.0)
        self.assertEqual(bebidas["ticket_com"], "37,00")
        self.assertEqual(bebidas["ticket_sem"], "20,00")
        self.assertEqual([b["nome"] for b in bebidas["ranking"]], ["Coca-Cola 2L", "Suco de laranja"])
        self.assertEqual(dados["grafico_top_bebidas"]["valores"], [1, 2])

        serie = dados["grafico_vendas_diarias"]
        self.assertEqual(sum(serie["bebidas"]), 17.0)
        self.assertEqual(sum(serie["bebidas_unidades"]), 3)
        self.assertEqual(sum(serie["por_pagamento"]["PIX"]), 37.0)
        self.assertEqual(sum(serie["por_pagamento"]["DINHEIRO"]), 20.0)
        self.assertEqual(sum(serie["taxas"]), 5.0)
        self.assertEqual(len(serie["bebidas"]), len(serie["labels"]))

    @override_settings(PALAVRAS_BEBIDAS=[])
    def test_sem_palavras_nada_e_bebida(self):
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("resumo_pedidos") + "?periodo=hoje").json()
        self.assertEqual(dados["total_bebidas"], "0,00")

    def test_estatisticas_e_pedidos_sem_nenhum_pedido(self):
        Pedidos.objects.all().delete()
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("dashboards")).status_code, 200)
        dados = self.client.get(reverse("dashboards") + "?ajax=1").json()
        self.assertEqual(dados["bebidas"]["receita"], "0,00")
        self.assertEqual(dados["bebidas"]["lider"], "Sem vendas")
        self.assertIsNone(dados["bebidas"]["ticket_diferenca"])
        resposta = self.client.get(reverse("pedidos"))
        self.assertEqual(resposta.context["total_bebidas"], "0,00")
        self.assertContains(resposta, "Nenhum pedido encontrado.")
        self.assertEqual(self.client.get(reverse("relatorio_pedidos", args=["bebidas"])).json()["itens"], [])


class RelatoriosDosCartoesTests(RelatorioBebidasTests):
    """Cada cartão do topo de Pedidos abre um relatório com os pedidos dele."""

    # Os testes herdados já rodam na classe mãe.
    test_relatorio_da_tela_de_pedidos = None
    test_bebidas_obedecem_ao_filtro_de_pagamento = None
    test_relatorio_grafico_de_bebidas_nas_estatisticas = None
    test_sem_palavras_nada_e_bebida = None
    test_estatisticas_e_pedidos_sem_nenhum_pedido = None

    def _relatorio(self, tipo, filtros="?periodo=hoje"):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("relatorio_pedidos", args=[tipo]) + filtros)
        self.assertEqual(resposta.status_code, 200, tipo)
        return resposta.json()

    def test_valor_de_cada_cartao(self):
        esperado = {
            "total": "57,00", "dinheiro": "20,00", "pix": "37,00", "cartao": "0,00", "misto": "0,00",
            "taxa": "5,00", "bebidas": "17,00", "retirada_dinheiro": "20,00",
            "entrega_dinheiro": "0,00", "caixa_dinheiro": "20,00",
        }
        for tipo, valor in esperado.items():
            with self.subTest(tipo=tipo):
                self.assertEqual(self._relatorio(tipo)["valor"], valor)

    def test_total_divide_por_pagamento_e_avisa_cancelados(self):
        dados = self._relatorio("total")
        quebra = {parte["rotulo"]: parte for parte in dados["quebra"]}
        self.assertEqual(quebra["Pix"]["valor"], "37,00")
        self.assertEqual(quebra["Pix"]["pct"], 64.9)
        self.assertEqual(quebra["Dinheiro"]["pct"], 35.1)
        self.assertEqual(dados["pedidos_total"], 2)
        self.assertIn("1 pedido cancelado", dados["nota"])

    def test_lista_de_pedidos_de_cada_relatorio(self):
        pix = self._relatorio("pix")
        self.assertEqual([p["id"] for p in pix["pedidos"]], [self.pedido_pix.id])
        taxa = self._relatorio("taxa")
        self.assertEqual(taxa["pedidos"][0]["valor"], "5,00")
        self.assertEqual(taxa["pedidos"][0]["detalhe"], "Rua das Flores, 12")
        bebidas = self._relatorio("bebidas")
        self.assertEqual([(p["id"], p["valor"]) for p in bebidas["pedidos"]], [(self.pedido_pix.id, "17,00")])
        self.assertEqual([i["nome"] for i in bebidas["itens"]], ["Coca-Cola 2L", "Suco de laranja"])
        self.assertEqual(self._relatorio("cartao")["pedidos"], [])

    def test_relatorio_obedece_ao_filtro_de_pagamento(self):
        self.assertEqual(self._relatorio("total", "?periodo=hoje&pagamento=DINHEIRO")["valor"], "20,00")

    @override_settings(PALAVRAS_BEBIDAS=[])
    def test_relatorio_de_bebidas_sem_palavras(self):
        dados = self._relatorio("bebidas")
        self.assertEqual(dados["valor"], "0,00")
        self.assertEqual(dados["pedidos"], [])

    def test_relatorio_desconhecido_e_login(self):
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse("relatorio_pedidos", args=["xyz"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("relatorio_pedidos", args=["pix"])).status_code, 302)
        self.assertEqual(self.client.get(reverse("pedido_detalhes", args=[self.pedido_pix.id])).status_code, 302)

    def test_detalhe_do_pedido(self):
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("pedido_detalhes", args=[self.pedido_pix.id])).json()
        self.assertEqual([i["nome"] for i in dados["itens"]], ["Marmita", "Coca-Cola 2L", "Suco de laranja"])
        self.assertEqual([i["bebida"] for i in dados["itens"]], [False, True, True])
        self.assertEqual(dados["total"], "37,00")
        self.assertEqual(dados["taxa"], "5,00")
        self.assertEqual(dados["total_final"], "42,00")
        self.assertEqual(dados["desconto"], "")
        self.assertEqual(dados["unidades"], 4)
        self.assertEqual(dados["whatsapp"], "https://wa.me/5511987654321")
        self.assertIn("google.com/maps", dados["mapa"])
        self.assertEqual((dados["etapa"], dados["proximo"]), (0, "PREPARO"))
        self.assertEqual(self.client.get(reverse("pedido_detalhes", args=[999999])).status_code, 404)

    def test_detalhe_mostra_o_desconto(self):
        Pedidos.objects.filter(pk=self.pedido_dinheiro.pk).update(total=Decimal("18.00"))
        self.client.force_login(self.usuario)
        dados = self.client.get(reverse("pedido_detalhes", args=[self.pedido_dinheiro.id])).json()
        self.assertEqual((dados["bruto"], dados["desconto"], dados["total"]), ("20,00", "2,00", "18,00"))
        self.assertEqual(dados["whatsapp"], "")
        self.assertEqual(dados["mapa"], "")

    def test_pagina_de_pedidos_leva_os_detalhes_e_os_cartoes(self):
        self.client.force_login(self.usuario)
        resposta = self.client.get(reverse("pedidos") + "?periodo=hoje")
        self.assertIn(str(self.pedido_pix.id), resposta.context["pedidos_detalhes"])
        self.assertContains(resposta, 'id="pedidosPaginaJson"')
        for tipo in ["total", "dinheiro", "pix", "cartao", "misto", "taxa", "bebidas",
                     "retirada_dinheiro", "entrega_dinheiro", "caixa_dinheiro"]:
            self.assertContains(resposta, f'data-relatorio="{tipo}"')


class FotosDasBebidasTests(TestCase):
    """Bebida aparece inteira na foto (classe foto-bebida); comida continua cobrindo o quadro."""

    def setUp(self):
        cache.clear()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        for nome_categoria, nome_produto in [("Bebidas", "Guaraná Lata"), ("Marmitas", "Marmita")]:
            categoria = CategoriaProdutos(nome_categoria=nome_categoria)
            categoria.imagem_categoria.name = "x.png"
            categoria.save()
            produto = Produtos(nome_produto=nome_produto, categoria=categoria, preco=Decimal("6.00"))
            produto.image_produto.name = f"produtos/{nome_produto}.png"
            produto.save()

    def _imagens(self, html):
        return {
            alt: "foto-bebida" in classes
            for alt, classes in re.findall(r'<img[^>]*alt="([^"]+)"[^>]*class="([^"]*)"', html, re.S)
            if alt in ("Guaraná Lata", "Marmita")
        }

    def test_cardapio(self):
        imagens = self._imagens(self.client.get(reverse("cardapio")).content.decode())
        self.assertEqual(imagens, {"Guaraná Lata": True, "Marmita": False})

    def test_caixa(self):
        self.client.force_login(self.usuario)
        imagens = self._imagens(self.client.get(reverse("caixa")).content.decode())
        self.assertEqual(imagens, {"Guaraná Lata": True, "Marmita": False})
