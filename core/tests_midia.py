"""Fotos no banco do site (core.midia), a rota /midia/banco/, o espelho da
Cloudinary e a restauração das fotos antigas (cópias locais e ZIP)."""
import contextlib
import importlib
import io
import os
import subprocess
import sys
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.conf import settings
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS, DatabaseError, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import auto_migracao, midia, restauracao
from .midia import ErroAoGuardarMidia, MidiaNoBanco, url_da_foto
from .models import ArquivoNoBanco, CategoriaProdutos, Produtos

ESTATICOS = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
NO_BANCO = {"default": {"BACKEND": "core.midia.MidiaNoBanco"}, "staticfiles": ESTATICOS}
NO_DISCO = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": ESTATICOS}
CLOUDINARY_DEMO = {"CLOUD_NAME": "demo", "API_KEY": "chave", "API_SECRET": "segredo"}
SEM_CLOUDINARY = {"CLOUD_NAME": None, "API_KEY": None, "API_SECRET": None}


def imagem(largura=60, altura=40, formato="PNG", ruido=False, cor=(200, 40, 40)):
    if ruido:
        figura = Image.frombytes("RGB", (largura, altura), os.urandom(largura * altura * 3))
    else:
        figura = Image.new("RGB", (largura, altura), cor)
    saida = io.BytesIO()
    figura.save(saida, formato)
    return saida.getvalue()


def abrir(dados):
    return Image.open(io.BytesIO(dados))


def resposta_http(status, conteudo=b"", cabecalhos=None):
    resposta = requests.Response()
    resposta.status_code = status
    resposta._content = conteudo
    resposta.headers.update(cabecalhos or {})
    return resposta


class ComProdutos(TestCase):
    def setUp(self):
        cache.clear()
        self.categoria = CategoriaProdutos(nome_categoria="Marmitas")
        self.categoria.imagem_categoria.name = "imagens_categorias/marmitas_q1w2e3"
        self.categoria.save()

    def produto(self, nome_foto, nome="Coca-Cola Lata", preco="6.00"):
        produto = Produtos(nome_produto=nome, categoria=self.categoria, preco=Decimal(preco))
        produto.image_produto.name = nome_foto
        produto.save()
        return produto


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="", CLOUDINARY_STORAGE=SEM_CLOUDINARY)
class ArmazenamentoNoBancoTests(TestCase):
    def setUp(self):
        self.storage = MidiaNoBanco()

    def test_guarda_le_e_apaga_no_banco(self):
        dados = imagem()
        nome = self.storage.save("produtos/Coca Lata.png", ContentFile(dados), max_length=100)
        self.assertRegex(nome, r"^banco/produtos/Coca_Lata-[0-9a-f]{10}\.png$")
        arquivo = ArquivoNoBanco.objects.get(nome=nome)
        self.assertEqual((arquivo.tipo, arquivo.tamanho, len(arquivo.sha256)), ("image/png", len(dados), 64))
        self.assertTrue(self.storage.exists(nome))
        self.assertEqual(self.storage.size(nome), len(dados))
        with self.storage.open(nome) as aberto:
            self.assertEqual(aberto.read(), dados)
        self.storage.delete(nome)
        self.assertFalse(self.storage.exists(nome))

    def test_foto_grande_vira_webp_de_no_maximo_2000px(self):
        nome = self.storage.save("produtos/grande.jpg", ContentFile(imagem(2600, 1300, "JPEG")), max_length=100)
        self.assertTrue(nome.endswith(".webp"))
        guardado = ArquivoNoBanco.objects.get(nome=nome)
        self.assertEqual(guardado.tipo, "image/webp")
        self.assertEqual(abrir(bytes(guardado.conteudo)).size, (2000, 1000))

    def test_foto_pesada_vira_webp_mesmo_pequena(self):
        dados = imagem(700, 700, "PNG", ruido=True)
        self.assertGreater(len(dados), midia.LIMITE_BYTES)
        nome = self.storage.save("produtos/pesada.png", ContentFile(dados), max_length=100)
        guardado = ArquivoNoBanco.objects.get(nome=nome)
        self.assertEqual(guardado.tipo, "image/webp")
        self.assertLess(guardado.tamanho, len(dados))
        self.assertEqual(abrir(bytes(guardado.conteudo)).size, (700, 700))

    def test_mesma_foto_duas_vezes_nao_duplica(self):
        dados = imagem()
        primeiro = self.storage.save("produtos/a.png", ContentFile(dados))
        segundo = self.storage.save("produtos/a.png", ContentFile(dados))
        self.assertEqual(primeiro, segundo)
        self.assertEqual(ArquivoNoBanco.objects.filter(nome__startswith="banco/").count(), 1)

    def test_nome_comprido_cabe_no_campo(self):
        nome = self.storage.save("produtos/" + "x" * 150 + ".png", ContentFile(imagem()), max_length=100)
        self.assertLessEqual(len(nome), 100)
        self.assertTrue(nome.startswith("banco/produtos/"))

    def test_arquivo_que_nao_e_imagem_vai_como_veio(self):
        nome = self.storage.save("docs/cardapio.txt", ContentFile(b"arroz e feijao"))
        guardado = ArquivoNoBanco.objects.get(nome=nome)
        self.assertEqual((bytes(guardado.conteudo), guardado.tipo), (b"arroz e feijao", "text/plain"))

    def test_falha_do_banco_vira_erro_claro(self):
        with mock.patch("core.midia.guardar_bytes", side_effect=DatabaseError("fora do ar")), \
                self.assertLogs("core.midia", "ERROR"):
            with self.assertRaises(ErroAoGuardarMidia) as contexto:
                self.storage.save("produtos/a.png", ContentFile(imagem()))
        self.assertIn("Não foi possível guardar a foto", str(contexto.exception))

    def test_url_do_banco_sem_consulta_nem_rede(self):
        with self.assertNumQueries(0), mock.patch("requests.get", side_effect=AssertionError("rede")):
            self.assertEqual(self.storage.url("banco/produtos/coca lata.webp"), "/midia/banco/produtos/coca%20lata.webp")


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="")
class LegadoTests(TestCase):
    """Nome antigo continua com o endereço da Cloudinary (sem rede, sem banco)."""

    def test_url_legada_da_cloudinary(self):
        with override_settings(CLOUDINARY_STORAGE=CLOUDINARY_DEMO):
            storage = MidiaNoBanco()
            with self.assertNumQueries(0), \
                    mock.patch("requests.get", side_effect=AssertionError("rede")), \
                    mock.patch("requests.head", side_effect=AssertionError("rede")):
                url = storage.url("media/produtos/coca_ab12cd")
        self.assertEqual(url, "https://res.cloudinary.com/demo/image/upload/v1/media/produtos/coca_ab12cd")

    @override_settings(CLOUDINARY_STORAGE=SEM_CLOUDINARY)
    def test_sem_cloudinary_o_legado_e_o_disco_e_o_site_sobe(self):
        storage = MidiaNoBanco()
        self.assertEqual(storage.url("produtos/acade.jpg"), "/media/produtos/acade.jpg")
        self.assertFalse(storage.exists("produtos/nao-existe.jpg"))

    @override_settings(CLOUDINARY_STORAGE=CLOUDINARY_DEMO)
    def test_apagar_legado_com_a_cloudinary_fora_nao_quebra(self):
        storage = MidiaNoBanco()
        with mock.patch.object(type(storage.legado), "delete", side_effect=RuntimeError("401 disabled")), \
                self.assertLogs("core.midia", "WARNING"):
            storage.delete("media/produtos/coca_ab12cd")  # não levanta

    @override_settings(CLOUDINARY_STORAGE=CLOUDINARY_DEMO)
    def test_open_e_exists_legados_vao_para_a_cloudinary(self):
        storage = MidiaNoBanco()
        with mock.patch("requests.head", return_value=resposta_http(401)) as head:
            self.assertFalse(storage.exists("media/produtos/coca_ab12cd"))
        head.assert_called_once()

    def test_url_da_foto(self):
        produto = Produtos(nome_produto="x")
        produto.image_produto.name = "banco/produtos/x-0123456789.webp"
        self.assertEqual(url_da_foto(produto.image_produto, 300), "/midia/banco/produtos/x-0123456789.webp?w=320")
        self.assertEqual(url_da_foto(produto.image_produto), "/midia/banco/produtos/x-0123456789.webp")
        produto.image_produto.name = ""
        self.assertEqual(url_da_foto(produto.image_produto, 320), "")
        with override_settings(CLOUDINARY_STORAGE=CLOUDINARY_DEMO):
            produto.image_produto.name = "media/produtos/coca_ab12cd"
            self.assertNotIn("?w=", url_da_foto(produto.image_produto, 320))


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="")
class RotaDaMidiaTests(TestCase):
    def setUp(self):
        self.dados = imagem(1200, 600, "PNG")
        self.nome = default_storage.save("produtos/prato.png", ContentFile(self.dados))
        self.url = default_storage.url(self.nome)

    def test_entrega_com_cache_de_um_ano_e_etag(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.content, self.dados)
        self.assertEqual(resposta["Content-Type"], "image/png")
        self.assertEqual(resposta["Cache-Control"], "public, max-age=31536000, s-maxage=31536000, immutable")
        self.assertEqual(resposta["ETag"], f'"{ArquivoNoBanco.objects.get(nome=self.nome).sha256}"')
        self.assertEqual(resposta["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Cookie", resposta.get("Vary", ""))
        self.assertFalse(resposta.cookies)

    def test_304_quando_o_navegador_ja_tem(self):
        etag = self.client.get(self.url)["ETag"]
        with self.assertNumQueries(1):  # só a consulta leve (sem o conteúdo)
            resposta = self.client.get(self.url, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(resposta.status_code, 304)
        self.assertEqual(resposta.content, b"")
        self.assertEqual(resposta["ETag"], etag)
        self.assertIn("immutable", resposta["Cache-Control"])
        self.assertEqual(self.client.get(self.url, HTTP_IF_NONE_MATCH=f"W/{etag}").status_code, 304)
        self.assertEqual(self.client.get(self.url, HTTP_IF_NONE_MATCH='"outro"').status_code, 200)

    def test_largura_permitida_entrega_webp_menor(self):
        resposta = self.client.get(self.url, {"w": 320})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "image/webp")
        self.assertEqual(abrir(resposta.content).size, (320, 160))
        self.assertTrue(resposta["ETag"].endswith('-w320"'))
        self.assertIn("immutable", resposta["Cache-Control"])

    def test_largura_fora_da_lista_redireciona_para_a_mais_proxima(self):
        resposta = self.client.get(self.url, {"w": 333})
        self.assertEqual(resposta.status_code, 301)
        self.assertEqual(resposta["Location"], f"{self.url}?w=480")
        self.assertEqual(self.client.get(self.url, {"w": "abc"})["Location"], self.url)
        self.assertEqual(self.client.get(self.url, {"w": 99999})["Location"], f"{self.url}?w=1600")

    def test_largura_maior_que_a_foto_entrega_a_original(self):
        resposta = self.client.get(self.url, {"w": 1600})
        self.assertEqual((resposta["Content-Type"], resposta.content), ("image/png", self.dados))

    def test_nome_desconhecido_da_404(self):
        resposta = self.client.get("/midia/banco/produtos/nao-existe.png")
        self.assertEqual(resposta.status_code, 404)
        self.assertNotIn("immutable", resposta["Cache-Control"])

    def test_foto_com_acento_no_nome_abre_mesmo_sem_decodificar_o_endereco(self):
        # Na Vercel o caminho chega como veio no endereço ("%C3%B4"), e as
        # fotos já guardadas com acento no nome davam 404.
        dados = imagem()
        for nome in ("banco/produtos/Almôndegas ao molho-2-eb148739af.png",
                     "banco/produtos/Fi\u0301gado-3-efe138e3f1.png"):  # acento separado (iPhone/Mac)
            self.assertTrue(midia.guardar_bytes(nome, dados, "image/png"))
        from .views_midia import servir_midia
        from django.test import RequestFactory
        for caminho in ("produtos/Alm%C3%B4ndegas%20ao%20molho-2-eb148739af.png",
                        "produtos/Almo%CC%82ndegas%20ao%20molho-2-eb148739af.png",
                        "produtos/F%C3%ADgado-3-efe138e3f1.png"):
            resposta = servir_midia(RequestFactory().get("/midia/banco/x"), caminho)
            self.assertEqual((resposta.status_code, resposta.content), (200, dados), caminho)
        self.assertEqual(self.client.get(default_storage.url("banco/produtos/Almôndegas ao molho-2-eb148739af.png")).status_code, 200)

    def test_nome_novo_nunca_leva_acento(self):
        nome = default_storage.save("produtos/Marmitex de linguiça toscana & purê-4.png", ContentFile(imagem()), max_length=100)
        self.assertRegex(nome, r"^banco/produtos/Marmitex_de_linguica_toscana_pure-4-[0-9a-f]{10}\.png$")
        self.assertEqual(self.client.get(default_storage.url(nome)).status_code, 200)

    def test_head_e_post(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_cache_em_disco_no_servidor_poupa_o_banco(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(MIDIA_CACHE_PASTA=pasta):
            self.addCleanup(midia.esquecer_memoria)
            self.client.get(self.url, {"w": 480})
            with self.assertNumQueries(0):  # a instância lembra o nome e tem a foto na pasta
                resposta = self.client.get(self.url, {"w": 480})
            self.assertEqual(abrir(resposta.content).size, (480, 240))
            self.assertEqual(len(os.listdir(pasta)), 1)

    def test_miniatura_fica_pronta_no_banco_e_sai_numa_consulta_so(self):
        primeira = self.client.get(self.url, {"w": 320})
        mini = ArquivoNoBanco.objects.get(nome=f"_mini/320/{self.nome}")
        self.assertEqual((mini.tipo, bytes(mini.conteudo)), ("image/webp", primeira.content))
        with self.assertNumQueries(1), mock.patch("core.midia.miniatura", side_effect=AssertionError("reduziu de novo")):
            segunda = self.client.get(self.url, {"w": 320})
        self.assertEqual((segunda.status_code, segunda.content), (200, primeira.content))
        self.assertEqual(segunda["ETag"], primeira["ETag"])
        self.assertIn("immutable", segunda["Cache-Control"])

    def test_foto_ja_pequena_tambem_fica_pronta(self):
        self.client.get(self.url, {"w": 1600})  # a foto tem 1200 px: vai a original
        with self.assertNumQueries(1):
            resposta = self.client.get(self.url, {"w": 1600})
        self.assertEqual((resposta["Content-Type"], resposta.content), ("image/png", self.dados))

    def test_previa_nasce_com_a_foto_e_some_com_ela(self):
        previa = ArquivoNoBanco.objects.get(nome=f"_mini/{midia.LARGURA_PREVIA}/{self.nome}")
        self.assertLess(previa.tamanho, 1500)
        self.assertEqual(abrir(bytes(previa.conteudo)).size, (20, 10))
        self.assertTrue(midia.previas([self.nome])[self.nome].startswith("data:image/webp;base64,"))
        self.client.get(self.url, {"w": 320})
        default_storage.delete(self.nome)
        self.assertEqual(ArquivoNoBanco.objects.count(), 0)
        self.assertEqual(self.client.get(self.url, {"w": 320}).status_code, 404)

    def test_foto_antiga_ganha_a_previa_na_primeira_entrega(self):
        ArquivoNoBanco.objects.filter(nome__startswith="_mini/").delete()
        self.client.get(self.url, {"w": 320})
        self.assertTrue(ArquivoNoBanco.objects.filter(nome=f"_mini/{midia.LARGURA_PREVIA}/{self.nome}").exists())

    def test_sem_cache_em_disco(self):
        self.client.get(self.url)
        with self.assertNumQueries(2):
            self.client.get(self.url)


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="")
class TelasComFotosDoBancoTests(ComProdutos):
    def setUp(self):
        super().setUp()
        self.usuario = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        nome = default_storage.save("produtos/coca.png", ContentFile(imagem()))
        self.produto(nome)

    def test_cardapio_pede_a_foto_na_largura_certa(self):
        html = self.client.get(reverse("cardapio")).content.decode()
        self.assertIn("/midia/banco/produtos/coca-", html)
        self.assertIn("?w=320", html)
        self.assertNotIn("this.style.display='none'", html)  # agora aparece o quadro
        self.assertIn("core/js/fotos.js", html)

    def test_cardapio_ja_vem_com_a_previa_da_foto(self):
        cache.clear()
        html = self.client.get(reverse("cardapio")).content.decode()
        self.assertIn("data-previa", html)
        self.assertEqual(html.count("data:image/webp;base64,"), 1)  # só na <img>, não no JSON
        self.assertIn('width="96" height="96"', html)
        self.assertIn('loading="eager"', html)
        self.assertNotIn("foto_nome", html)

    def test_caixa_pede_a_foto_na_largura_certa(self):
        self.client.force_login(self.usuario)
        html = self.client.get(reverse("caixa")).content.decode()
        self.assertIn("?w=480", html)
        self.assertIn("core/js/fotos.js", html)


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="")
class EnvioPeloAdminTests(ComProdutos):
    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123"))

    def _enviar(self):
        return self.client.post(reverse("admin:core_produtos_add"), {
            "nome_produto": "Suco de Laranja",
            "image_produto": SimpleUploadedFile("suco.jpg", imagem(80, 80, "JPEG"), content_type="image/jpeg"),
            "codigo": 7,
            "preco": "8.00",
            "categoria": self.categoria.pk,
            "ativo": "on",
        })

    def test_foto_enviada_vai_para_o_banco(self):
        resposta = self._enviar()
        self.assertEqual(resposta.status_code, 302)
        produto = Produtos.objects.get(nome_produto="Suco de Laranja")
        self.assertTrue(produto.image_produto.name.startswith("banco/produtos/suco-"))
        self.assertEqual(self.client.get(produto.image_produto.url)["Content-Type"], "image/jpeg")

    def test_falha_ao_guardar_mostra_aviso_e_nao_da_500(self):
        with mock.patch("core.midia.guardar_bytes", side_effect=DatabaseError("fora do ar")), \
                self.assertLogs("core", "ERROR"):
            resposta = self._enviar()
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não foi possível guardar a foto agora")
        self.assertContains(resposta, "Detalhe técnico: DatabaseError: fora do ar")
        self.assertContains(resposta, "Suco de Laranja")  # o que foi digitado continua no formulário
        self.assertFalse(Produtos.objects.filter(nome_produto="Suco de Laranja").exists())

    def test_admin_carrega_o_quadro_de_foto_em_atualizacao(self):
        self.produto("media/produtos/coca_ab12cd")
        html = self.client.get(reverse("admin:core_produtos_changelist")).content.decode()
        self.assertIn("core/js/fotos.js", html)


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="", CLOUDINARY_STORAGE=CLOUDINARY_DEMO)
class EspelharMidiaTests(ComProdutos):
    def test_copia_da_cloudinary_e_e_idempotente(self):
        coca = self.produto("media/produtos/coca_ab12cd")
        foto = imagem(50, 50, "PNG", cor=(10, 200, 10))
        with mock.patch("requests.Session.get", return_value=resposta_http(200, foto)) as get:
            call_command("espelhar_midia", stdout=io.StringIO())
        self.assertEqual(get.call_count, 2)  # a foto do produto e a da categoria
        self.assertIn(
            "https://res.cloudinary.com/demo/image/upload/v1/media/produtos/coca_ab12cd",
            [chamada.args[0] for chamada in get.call_args_list],
        )
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "banco/media/produtos/coca_ab12cd")
        self.assertEqual(bytes(ArquivoNoBanco.objects.get(nome=coca.image_produto.name).conteudo), foto)
        self.assertEqual(self.client.get(coca.image_produto.url).content, foto)

        with mock.patch("requests.Session.get", side_effect=AssertionError("não devia baixar de novo")):
            call_command("espelhar_midia", stdout=io.StringIO())
        self.assertEqual(ArquivoNoBanco.objects.count(), 2)

    def test_cloudinary_desativada_para_e_avisa(self):
        coca = self.produto("media/produtos/coca_ab12cd")
        recusa = resposta_http(401, b"", {"x-cld-error": "cloud_name demo is disabled"})
        with mock.patch("requests.Session.get", return_value=recusa) as get:
            with self.assertRaises(CommandError) as contexto:
                call_command("espelhar_midia", stdout=io.StringIO())
        self.assertIn("disabled", str(contexto.exception))
        self.assertEqual(get.call_count, 1)  # parou na primeira
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "media/produtos/coca_ab12cd")
        self.assertFalse(ArquivoNoBanco.objects.exists())

    def test_arquivo_sumido_na_cloudinary_nao_para_o_resto(self):
        self.produto("media/produtos/coca_ab12cd")
        respostas = [resposta_http(404), resposta_http(200, imagem())]
        saida = io.StringIO()
        with mock.patch("requests.Session.get", side_effect=respostas):
            call_command("espelhar_midia", stdout=saida)
        self.assertIn("não existe na Cloudinary", saida.getvalue())
        self.assertEqual(ArquivoNoBanco.objects.count(), 1)

    def test_limite_por_rodada(self):
        self.produto("media/produtos/coca_ab12cd")
        saida = io.StringIO()
        with mock.patch("requests.Session.get", return_value=resposta_http(200, imagem())):
            call_command("espelhar_midia", "--limite", "1", stdout=saida)
        self.assertEqual(ArquivoNoBanco.objects.count(), 1)
        self.assertIn("ainda por copiar: 1", saida.getvalue())

    @override_settings(CLOUDINARY_STORAGE=SEM_CLOUDINARY)
    def test_sem_nome_da_conta_avisa(self):
        with self.assertRaises(CommandError):
            call_command("espelhar_midia", stdout=io.StringIO())


class IdentidadeTests(TestCase):
    def test_tira_prefixos_e_extensao(self):
        self.assertEqual(restauracao.identidade("media/produtos/coca_ab12cd"), ("produtos", "coca_ab12cd", ""))
        self.assertEqual(restauracao.identidade("banco/media/produtos/coca_ab12cd"), ("produtos", "coca_ab12cd", ""))
        self.assertEqual(restauracao.identidade("produtos/Acade.JPG"), ("produtos", "Acade", ".jpg"))
        self.assertEqual(restauracao.identidade("produtos/coca.lata_ab12cd"), ("produtos", "coca.lata_ab12cd", ""))
        self.assertEqual(
            restauracao.identidade("https://res.cloudinary.com/demo/image/upload/v17/media/produtos/coca_ab12cd.jpg"),
            ("produtos", "coca_ab12cd", ".jpg"),
        )

    def test_niveis_de_certeza(self):
        indice = restauracao.Indice(["media/produtos/coca_ab12cd", "produtos/acade.jpg", "produtos/frango_grelha.jpg"])
        self.assertEqual(indice.procurar("media/produtos/coca_ab12cd.png").nivel, restauracao.EXATO)
        self.assertEqual(indice.procurar("export/media/produtos/coca_ab12cd.png").nivel, restauracao.EXATO)
        self.assertEqual(indice.procurar("coca_ab12cd.jpg").nivel, restauracao.MESMO_NOME)
        self.assertEqual(indice.procurar("produtos/coca.jpg").nivel, restauracao.SEM_SUFIXO)
        self.assertEqual(indice.procurar("coca.jpg").nivel, restauracao.SEM_SUFIXO)
        self.assertEqual(indice.procurar("produtos/acade.jpeg").nivel, restauracao.EXATO)
        # Nunca uma foto errada:
        self.assertEqual(indice.procurar("outros/coca_ab12cd.png").nivel, 0)   # outra pasta
        self.assertEqual(indice.procurar("produtos/acade.png").nivel, 0)       # outra extensão
        self.assertEqual(indice.procurar("produtos/frango.jpg").nivel, 0)      # "_grelha" não é sufixo sorteado
        dois = restauracao.Indice(["produtos/coca_ab12cd", "produtos/coca_zz99yy"])
        achado = dois.procurar("coca.jpg")
        self.assertEqual(achado.nivel, 0)
        self.assertTrue(achado.motivo)


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="")
class RestaurarCopiasLocaisTests(ComProdutos):
    def setUp(self):
        super().setUp()
        self.temporaria = tempfile.TemporaryDirectory()
        self.raiz = Path(self.temporaria.name)
        for pasta in ("projeto", "media", "static"):
            (self.raiz / pasta).mkdir()
        self.ajustes = override_settings(
            BASE_DIR=self.raiz / "projeto", MEDIA_ROOT=self.raiz / "media", STATIC_ROOT=self.raiz / "static",
        )
        self.ajustes.enable()

    def tearDown(self):
        self.ajustes.disable()
        self.temporaria.cleanup()
        super().tearDown()

    def copia(self, caminho, dados, raiz="projeto"):
        destino = self.raiz / raiz / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)

    def test_restaura_a_copia_do_projeto_sem_o_sufixo_da_cloudinary(self):
        coca = self.produto("media/produtos/coca_ab12cd")
        foto = imagem(30, 30, "PNG", cor=(1, 2, 3))
        self.copia("produtos/coca.png", foto)
        self.copia("imagens_categorias/marmitas.jpg", imagem(30, 30, "JPEG"), raiz="media")
        relatorio = restauracao.restaurar_copias_locais()
        self.assertEqual(len(relatorio.restauradas), 2)
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "banco/media/produtos/coca_ab12cd")
        self.assertEqual(self.client.get(coca.image_produto.url).content, foto)
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.imagem_categoria.name, "banco/imagens_categorias/marmitas_q1w2e3")
        # Idempotente: a segunda rodada não faz nada.
        self.assertEqual(restauracao.restaurar_copias_locais().restauradas, [])
        self.assertEqual(ArquivoNoBanco.objects.count(), 2)

    def test_na_duvida_nao_associa(self):
        um = self.produto("produtos/coca_ab12cd", nome="Coca 1")
        dois = self.produto("produtos/coca_zz99yy", nome="Coca 2")
        self.copia("produtos/coca.png", imagem())
        relatorio = restauracao.restaurar_copias_locais()
        self.assertEqual(relatorio.restauradas, [])
        self.assertEqual(len(relatorio.duvidas), 1)
        um.refresh_from_db()
        dois.refresh_from_db()
        self.assertEqual((um.image_produto.name, dois.image_produto.name), ("produtos/coca_ab12cd", "produtos/coca_zz99yy"))

    def test_duas_copias_diferentes_da_mesma_foto_ficam_de_fora(self):
        coca = self.produto("produtos/coca_ab12cd")
        self.copia("produtos/coca.png", imagem(cor=(1, 1, 1)))
        self.copia("produtos/coca.png", imagem(cor=(9, 9, 9)), raiz="media")
        self.assertEqual(restauracao.restaurar_copias_locais().restauradas, [])
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "produtos/coca_ab12cd")

    def test_copia_que_nao_e_imagem_nao_entra(self):
        coca = self.produto("produtos/coca_ab12cd")
        self.copia("produtos/coca.png", b"isto nao e uma foto")
        relatorio = restauracao.restaurar_copias_locais()
        self.assertEqual(len(relatorio.erros), 1)
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "produtos/coca_ab12cd")

    def test_outra_pasta_nao_serve(self):
        coca = self.produto("produtos/coca_ab12cd")
        self.copia("imagens_categorias/coca.png", imagem())
        restauracao.restaurar_copias_locais()
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "produtos/coca_ab12cd")

    def test_roda_sozinho_depois_do_migrate(self):
        coca = self.produto("produtos/coca_ab12cd")
        self.copia("produtos/coca.png", imagem())
        with override_settings(STORAGES=NO_DISCO):
            restauracao.restaurar_ao_migrar(verbosity=0)  # desenvolvimento: não mexe
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "produtos/coca_ab12cd")
        restauracao.restaurar_ao_migrar(verbosity=0)
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "banco/produtos/coca_ab12cd")

    def test_migracao_automatica_ao_iniciar_tambem_restaura(self):
        coca = self.produto("produtos/coca_ab12cd")
        self.copia("produtos/coca.png", imagem())
        with mock.patch.object(auto_migracao, "pendentes", return_value=[(migracao_falsa(), False)]), \
                contextlib.redirect_stdout(io.StringIO()), self.assertLogs("core.auto_migracao", "WARNING"):
            self.assertEqual(auto_migracao.migrar_pendentes(forcar=True), ["core.0015_arquivonobanco"])
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "banco/produtos/coca_ab12cd")

    def test_restaurar_ao_migrar_nunca_derruba(self):
        with mock.patch("core.restauracao.restaurar_copias_locais", side_effect=DatabaseError("sem tabela")), \
                self.assertLogs("core.restauracao", "WARNING"):
            restauracao.restaurar_ao_migrar(verbosity=0)

    def test_comando_e_relatorio(self):
        self.produto("media/produtos/coca_ab12cd")
        self.copia("produtos/coca.png", imagem())
        saida = io.StringIO()
        call_command("restaurar_midia", stdout=saida)
        self.assertIn("Restauradas: 1", saida.getvalue())
        saida = io.StringIO()
        call_command("restaurar_midia", "--relatorio", stdout=saida)
        texto = saida.getvalue()
        self.assertIn("Fotos ainda faltando: 1", texto)
        self.assertIn(f"Categoria de Produto #{self.categoria.pk} (Marmitas) · imagem_categoria · imagens_categorias/marmitas_q1w2e3", texto)
        self.assertNotIn("coca_ab12cd", texto)

    def test_relatorio_mostra_foto_do_banco_que_sumiu(self):
        produto = self.produto("banco/produtos/sumiu-0123456789.webp")
        linhas = restauracao.descrever(restauracao.faltando())
        self.assertIn(("Produto", produto.pk, "image_produto", "banco/produtos/sumiu-0123456789.webp"),
                      [(l["modelo"], l["pk"], l["campo"], l["nome"]) for l in linhas])

    def test_comando_nao_grava_sem_o_banco_ligado_e_nao_derruba_o_deploy(self):
        coca = self.produto("produtos/coca_ab12cd")
        self.copia("produtos/coca.png", imagem())
        saida = io.StringIO()
        with override_settings(STORAGES=NO_DISCO):
            call_command("restaurar_midia", stdout=saida)  # sem erro: só avisa
            with self.assertRaises(CommandError):
                call_command("restaurar_midia", "--zip", str(self.raiz), stdout=io.StringIO())
        self.assertIn("Nada foi alterado", saida.getvalue())
        coca.refresh_from_db()
        self.assertEqual(coca.image_produto.name, "produtos/coca_ab12cd")


def zip_com(arquivos):
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as zip_:
        for nome, dados in arquivos.items():
            zip_.writestr(nome, dados)
    return saida.getvalue()


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="", SERVERLESS=False)
class ImportarZipTests(ComProdutos):
    def setUp(self):
        super().setUp()
        self.coca = self.produto("media/produtos/coca_ab12cd")
        self.foto = imagem(40, 40, "JPEG")
        self.zip = zip_com({
            "media/produtos/coca_ab12cd.jpg": self.foto,
            "media/outros/logo_k9k9k9.png": imagem(),
            "__MACOSX/media/produtos/._coca_ab12cd.jpg": b"lixo",
        })
        self.dono = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")

    def test_comando_com_zip(self):
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as arquivo:
            arquivo.write(self.zip)
        try:
            saida = io.StringIO()
            call_command("restaurar_midia", "--zip", arquivo.name, stdout=saida)
        finally:
            os.unlink(arquivo.name)
        self.assertIn("Restauradas: 1", saida.getvalue())
        self.assertIn("sem uso no site: 1", saida.getvalue())
        self.coca.refresh_from_db()
        self.assertEqual(self.coca.image_produto.name, "banco/media/produtos/coca_ab12cd")
        self.assertEqual(bytes(ArquivoNoBanco.objects.get(nome=self.coca.image_produto.name).conteudo), self.foto)

    def test_zip_sem_pastas_tambem_serve(self):
        relatorio = restauracao.restaurar_zip(io.BytesIO(zip_com({"coca_ab12cd.jpg": self.foto})))
        self.assertEqual(len(relatorio.restauradas), 1)

    def test_somente_a_equipe_ve_a_pagina(self):
        url = reverse("restaurar_fotos")
        self.assertEqual(self.client.get(url).status_code, 302)  # vai para o login
        self.client.force_login(User.objects.create_user("caixa", password="senha-segura-123"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.dono)
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "media/produtos/coca_ab12cd")
        self.assertContains(resposta, "Coca-Cola Lata")

    def test_pagina_verifica_e_recebe_o_zip(self):
        self.client.force_login(self.dono)
        url = reverse("restaurar_fotos")
        verificado = self.client.post(
            f"{url}?acao=verificar", data={"caminhos": ["media/produtos/coca_ab12cd.jpg", "x/y.png"]},
            content_type="application/json", HTTP_X_REQUESTED_WITH="fetch",
        ).json()
        self.assertEqual(verificado["situacoes"]["media/produtos/coca_ab12cd.jpg"]["situacao"], "restaurar")
        self.assertEqual(verificado["situacoes"]["x/y.png"]["situacao"], "sem_uso")

        resposta = self.client.post(
            f"{url}?acao=enviar", {"arquivo": SimpleUploadedFile("cloudinary.zip", self.zip, "application/zip")},
            HTTP_X_REQUESTED_WITH="fetch",
        ).json()
        self.assertTrue(resposta["ok"])
        self.assertEqual(resposta["relatorio"]["restauradas"][0]["nome_antigo"], "media/produtos/coca_ab12cd")
        self.assertEqual(resposta["faltando"], 1)  # ainda falta a foto da categoria
        self.coca.refresh_from_db()
        self.assertEqual(self.coca.image_produto.name, "banco/media/produtos/coca_ab12cd")

    def test_pagina_recebe_uma_foto_por_vez_com_o_caminho_do_zip(self):
        self.client.force_login(self.dono)
        resposta = self.client.post(
            f"{reverse('restaurar_fotos')}?acao=enviar",
            {"arquivo": SimpleUploadedFile("coca_ab12cd.jpg", self.foto, "image/jpeg"), "caminho": "media/produtos/coca_ab12cd.jpg"},
            HTTP_X_REQUESTED_WITH="fetch",
        ).json()
        self.assertEqual(len(resposta["relatorio"]["restauradas"]), 1)
        repetida = self.client.post(
            f"{reverse('restaurar_fotos')}?acao=enviar",
            {"arquivo": SimpleUploadedFile("coca_ab12cd.jpg", self.foto, "image/jpeg"), "caminho": "media/produtos/coca_ab12cd.jpg"},
            HTTP_X_REQUESTED_WITH="fetch",
        ).json()
        self.assertEqual(repetida["relatorio"]["ja_restauradas"], ["media/produtos/coca_ab12cd.jpg"])

    @override_settings(SERVERLESS=True)
    def test_na_vercel_recusa_envio_acima_do_limite(self):
        self.client.force_login(self.dono)
        grande = SimpleUploadedFile("coca_ab12cd.jpg", b"0" * (restauracao.LIMITE_ENVIO_SERVERLESS + 1), "image/jpeg")
        resposta = self.client.post(f"{reverse('restaurar_fotos')}?acao=enviar", {"arquivo": grande},
                                    HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertIn("MB por envio", resposta["relatorio"]["erros"][0]["mensagem"])
        self.coca.refresh_from_db()
        self.assertEqual(self.coca.image_produto.name, "media/produtos/coca_ab12cd")

    def test_pagina_sem_o_banco_ligado_nao_grava(self):
        self.client.force_login(self.dono)
        with override_settings(STORAGES=NO_DISCO):
            resposta = self.client.post(
                f"{reverse('restaurar_fotos')}?acao=enviar",
                {"arquivo": SimpleUploadedFile("c.zip", self.zip, "application/zip")}, HTTP_X_REQUESTED_WITH="fetch",
            )
        self.assertEqual(resposta.status_code, 400)
        self.coca.refresh_from_db()
        self.assertEqual(self.coca.image_produto.name, "media/produtos/coca_ab12cd")

    def test_falha_inesperada_nao_vira_500(self):
        self.client.force_login(self.dono)
        with mock.patch("core.restauracao.restaurar_zip", side_effect=RuntimeError("pane")), \
                self.assertLogs("core.views_midia", "ERROR"):
            resposta = self.client.post(
                f"{reverse('restaurar_fotos')}?acao=enviar",
                {"arquivo": SimpleUploadedFile("c.zip", self.zip, "application/zip")}, HTTP_X_REQUESTED_WITH="fetch",
            )
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("Nada se perdeu", resposta.json()["mensagem"])


def migracao_falsa(app="core", nome="0015_arquivonobanco"):
    falsa = mock.Mock(app_label=app)
    falsa.name = nome
    return falsa


def imagem_heic(largura=64, altura=48):
    saida = io.BytesIO()
    Image.new("RGB", (largura, altura), (30, 120, 200)).save(saida, "HEIF")
    return saida.getvalue()


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="", CLOUDINARY_STORAGE=SEM_CLOUDINARY, SERVERLESS=False)
class EscolherFotoTests(ComProdutos):
    """Você tem a foto (com qualquer nome) e diz de quem ela é."""

    def setUp(self):
        super().setUp()
        self.coca = self.produto("media/produtos/coca_ab12cd")
        self.dono = User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123")
        self.client.force_login(self.dono)
        self.url = reverse("restaurar_fotos")

    def _definir(self, arquivo, **campos):
        dados = {"arquivo": arquivo, "modelo": "core.Produtos", "pk": self.coca.pk, "campo": "image_produto"}
        dados.update(campos)
        return self.client.post(f"{self.url}?acao=definir", dados, HTTP_X_REQUESTED_WITH="fetch")

    def test_foto_com_qualquer_nome_vai_para_o_produto_escolhido(self):
        resposta = self._definir(SimpleUploadedFile("IMG_4032.JPG", imagem(90, 60, "JPEG"), "image/jpeg"))
        self.assertEqual(resposta.status_code, 200, resposta.content)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["faltando"], 1)  # só a foto da categoria
        self.coca.refresh_from_db()
        self.assertTrue(self.coca.image_produto.name.startswith("banco/produtos/IMG_4032-"))
        entregue = self.client.get(self.coca.image_produto.url)
        self.assertEqual(entregue.status_code, 200)
        self.assertEqual(entregue["Content-Type"], "image/jpeg")
        self.assertIn("immutable", entregue["Cache-Control"])

    def test_tambem_serve_para_a_categoria(self):
        resposta = self._definir(
            SimpleUploadedFile("marmita nova.png", imagem(), "image/png"),
            modelo="core.CategoriaProdutos", pk=self.categoria.pk, campo="imagem_categoria",
        )
        self.assertTrue(resposta.json()["ok"])
        self.categoria.refresh_from_db()
        self.assertTrue(self.categoria.imagem_categoria.name.startswith("banco/imagens_categorias/"))

    def test_a_pagina_mostra_o_botao_de_cada_foto_que_falta(self):
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "data-definir")
        self.assertContains(resposta, 'data-chave="core.Produtos"')
        self.assertContains(resposta, "preparar_foto.js")
        linhas = self.client.get(f"{self.url}?formato=json").json()
        self.assertEqual({l["chave"] for l in linhas["faltando"]}, {"core.Produtos", "core.CategoriaProdutos"})

    def test_o_que_nao_e_imagem_nao_entra_e_nada_muda(self):
        resposta = self._definir(SimpleUploadedFile("nota.jpg", b"isto nao e imagem", "image/jpeg"))
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("imagem", resposta.json()["mensagem"])
        self.coca.refresh_from_db()
        self.assertEqual(self.coca.image_produto.name, "media/produtos/coca_ab12cd")

    def test_campo_ou_cadastro_errado_e_recusado(self):
        foto = lambda: SimpleUploadedFile("a.png", imagem(), "image/png")
        self.assertEqual(self._definir(foto(), campo="nome_produto").status_code, 400)
        self.assertEqual(self._definir(foto(), modelo="auth.User", campo="password").status_code, 400)
        self.assertEqual(self._definir(foto(), pk=999999).status_code, 400)
        self.assertEqual(ArquivoNoBanco.objects.count(), 0)

    def test_so_a_equipe_define_foto(self):
        self.client.force_login(User.objects.create_user("caixa", password="senha-segura-123"))
        resposta = self._definir(SimpleUploadedFile("a.png", imagem(), "image/png"))
        self.assertEqual(resposta.status_code, 403)

    def test_foto_do_iphone_heic_vira_webp(self):
        resposta = self._definir(SimpleUploadedFile("IMG_0001.HEIC", imagem_heic(), "image/heic"))
        self.assertEqual(resposta.status_code, 200, resposta.content)
        self.coca.refresh_from_db()
        self.assertTrue(self.coca.image_produto.name.endswith(".webp"))
        entregue = self.client.get(self.coca.image_produto.url)
        self.assertEqual(entregue["Content-Type"], "image/webp")
        self.assertEqual(abrir(entregue.content).size, (64, 48))

    def test_admin_aceita_heic(self):
        resposta = self.client.post(reverse("admin:core_produtos_change", args=[self.coca.pk]), {
            "nome_produto": "Coca-Cola Lata",
            "image_produto": SimpleUploadedFile("IMG_0002.heic", imagem_heic(), content_type="image/heic"),
            "codigo": 7, "preco": "6.00", "categoria": self.categoria.pk, "ativo": "on",
        })
        self.assertEqual(resposta.status_code, 302, resposta.content[:2000])
        self.coca.refresh_from_db()
        self.assertTrue(self.coca.image_produto.name.endswith(".webp"))

    def test_formato_que_o_navegador_nao_mostra_vira_webp(self):
        dados, tipo, extensao = midia.preparar_arquivo(imagem(50, 30, "BMP"), "produtos/foto.bmp")
        self.assertEqual((tipo, extensao), ("image/webp", ".webp"))
        self.assertEqual(abrir(dados).format, "WEBP")

    def test_admin_carrega_o_redutor_de_fotos(self):
        resposta = self.client.get(reverse("admin:core_produtos_add"))
        self.assertContains(resposta, "core/js/preparar_foto.js")

    def test_banco_fora_do_ar_mostra_aviso_e_nao_da_500(self):
        with mock.patch.object(restauracao, "faltando", side_effect=DatabaseError("fora do ar")):
            resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "O banco não respondeu")

    def test_falha_do_banco_no_admin_nao_quebra_a_transacao(self):
        with mock.patch.object(midia, "guardar_bytes", side_effect=DatabaseError("fora do ar")):
            resposta = self.client.post(reverse("admin:core_produtos_change", args=[self.coca.pk]), {
                "nome_produto": "Coca-Cola Lata",
                "image_produto": SimpleUploadedFile("nova.png", imagem(), content_type="image/png"),
                "codigo": 7, "preco": "6.00", "categoria": self.categoria.pk, "ativo": "on",
            })
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não foi possível guardar a foto agora")


@override_settings(STORAGES=NO_BANCO, MIDIA_CACHE_PASTA="", CLOUDINARY_STORAGE=SEM_CLOUDINARY, SERVERLESS=False)
class TabelaQueFaltaTests(TransactionTestCase):
    """Em produção a migração 0015 não rodou e todo envio falhava: guardar
    foto não pode depender dela."""

    def setUp(self):
        cache.clear()
        self._apagar_tabela()
        self.categoria = CategoriaProdutos(nome_categoria="Bebidas")
        self.categoria.imagem_categoria.name = "media/imagens_categorias/beb_aa11bb"
        self.categoria.save()
        self.refri = Produtos(nome_produto="REFRI-MINI 200ml", categoria=self.categoria, preco=Decimal("2.00"), codigo=555)
        self.refri.image_produto.name = "media/produtos/refriii_gdff30"
        self.refri.save()
        self.client.force_login(User.objects.create_superuser("dono", "dono@x.com", "senha-segura-123"))

    def tearDown(self):
        midia.garantir_tabela()

    @staticmethod
    def _apagar_tabela():
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {ArquivoNoBanco._meta.db_table}")

    def test_admin_guarda_a_foto_mesmo_sem_a_tabela(self):
        self.assertFalse(midia.tabela_existe())
        resposta = self.client.post(reverse("admin:core_produtos_change", args=[self.refri.pk]), {
            "nome_produto": "REFRI-MINI 200ml",
            "image_produto": SimpleUploadedFile("refri.jpg", imagem(80, 80, "JPEG"), content_type="image/jpeg"),
            "codigo": 555, "preco": "2.00", "categoria": self.categoria.pk, "ativo": "on",
        })
        self.assertEqual(resposta.status_code, 302, resposta.content[:3000])
        self.refri.refresh_from_db()
        self.assertTrue(self.refri.image_produto.name.startswith("banco/produtos/refri-"))
        self.assertEqual(self.client.get(self.refri.image_produto.url).status_code, 200)

    def test_foto_pedida_sem_a_tabela_da_404_e_nao_500(self):
        self.assertEqual(self.client.get("/midia/banco/produtos/x.jpg").status_code, 404)
        self.assertTrue(midia.tabela_existe())

    def test_restaurar_fotos_abre_e_testa_o_armazenamento(self):
        resposta = self.client.get(reverse("restaurar_fotos"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Armazenamento testado agora")
        self.assertTrue(midia.tabela_existe())
        self.assertEqual(ArquivoNoBanco.objects.count(), 0)  # o teste não deixa lixo

    def test_teste_do_armazenamento_conta_o_erro_de_verdade(self):
        with mock.patch.object(ArquivoNoBanco.objects, "create", side_effect=DatabaseError("cannot execute INSERT in a read-only transaction")):
            self.assertIn("read-only", midia.testar_armazenamento())
        resposta_ok = midia.testar_armazenamento()
        self.assertEqual(resposta_ok, "")

    def test_migracao_0015_aceita_a_tabela_ja_criada(self):
        midia.garantir_tabela()
        call_command("migrate", "core", "0014", fake=True, verbosity=0)
        call_command("migrate", "core", verbosity=0)
        self.assertTrue(midia.tabela_existe())


class _CursorFalso:
    def __init__(self, conexao):
        self.conexao = conexao

    def __enter__(self):
        return self

    def __exit__(self, *erro):
        return False

    def execute(self, sql, parametros=None):
        self.conexao.sql.append((sql, parametros))


class _PostgresFalso:
    vendor = "postgresql"

    def __init__(self):
        self.sql = []
        self.fechada = False

    def cursor(self):
        return _CursorFalso(self)

    def close(self):
        self.fechada = True


@override_settings(MIGRAR_AO_INICIAR=False)
class MigracaoAoIniciarTests(TestCase):
    """core/auto_migracao.py: a 0015 (fotos no banco) entra sozinha na Vercel."""

    def test_desligado_nao_consulta_nada(self):
        with mock.patch.object(auto_migracao, "pendentes") as pendentes:
            self.assertEqual(auto_migracao.migrar_pendentes(), [])
        pendentes.assert_not_called()

    def test_sem_pendencias_nao_faz_nada(self):
        with mock.patch("django.core.management.call_command") as migrate:
            self.assertEqual(auto_migracao.migrar_pendentes(forcar=True), [])
        migrate.assert_not_called()

    @override_settings(MIGRAR_AO_INICIAR=True)
    def test_aplica_o_que_falta(self):
        with mock.patch.object(auto_migracao, "pendentes", return_value=[(migracao_falsa(), False)]), \
                mock.patch("django.core.management.call_command") as migrate, \
                self.assertLogs("core.auto_migracao", "WARNING"):
            self.assertEqual(auto_migracao.migrar_pendentes(), ["core.0015_arquivonobanco"])
        migrate.assert_called_once_with("migrate", interactive=False, verbosity=1)

    def test_no_postgres_so_uma_instancia_migra(self):
        conexao = _PostgresFalso()
        plano = [(migracao_falsa(), False)]
        with mock.patch.object(auto_migracao, "connections", {DEFAULT_DB_ALIAS: conexao}), \
                mock.patch.object(auto_migracao, "pendentes", side_effect=[plano, plano]) as pendentes, \
                mock.patch("django.core.management.call_command") as migrate, \
                self.assertLogs("core.auto_migracao", "WARNING"):
            self.assertEqual(auto_migracao.migrar_pendentes(forcar=True), ["core.0015_arquivonobanco"])
        self.assertEqual(conexao.sql, [("SELECT pg_advisory_xact_lock(%s)", [auto_migracao.CHAVE_TRAVA])])
        self.assertEqual(pendentes.call_count, 2)  # confere de novo depois de pegar a trava
        migrate.assert_called_once()
        self.assertTrue(conexao.fechada)

    def test_no_postgres_outra_instancia_ja_migrou(self):
        conexao = _PostgresFalso()
        with mock.patch.object(auto_migracao, "connections", {DEFAULT_DB_ALIAS: conexao}), \
                mock.patch.object(auto_migracao, "pendentes", side_effect=[[(migracao_falsa(), False)], []]), \
                mock.patch("django.core.management.call_command") as migrate:
            self.assertEqual(auto_migracao.migrar_pendentes(forcar=True), [])
        migrate.assert_not_called()
        self.assertEqual(len(conexao.sql), 1)

    def test_falha_vai_para_o_log_e_o_site_sobe(self):
        with mock.patch.object(auto_migracao, "pendentes", return_value=[(migracao_falsa(), False)]), \
                mock.patch("django.core.management.call_command", side_effect=DatabaseError("pooler fora")), \
                self.assertLogs("core.auto_migracao", "ERROR") as log:
            self.assertEqual(auto_migracao.migrar_pendentes(forcar=True), [])
        self.assertIn("python manage.py migrate", log.output[0])
        with mock.patch.object(auto_migracao, "pendentes", side_effect=DatabaseError("sem banco")), \
                self.assertLogs("core.auto_migracao", "ERROR"):
            self.assertEqual(auto_migracao.migrar_pendentes(forcar=True), [])

    def test_wsgi_migra_ao_subir(self):
        with mock.patch("core.auto_migracao.migrar_pendentes") as migrar:
            sys.modules.pop("rest.wsgi", None)
            importlib.import_module("rest.wsgi")
        migrar.assert_called_once_with()

    def test_ligado_por_padrao_so_no_site_no_ar_da_vercel(self):
        def valor(**variaveis):
            ambiente = {
                k: v for k, v in os.environ.items()
                if k not in ("VERCEL", "CI", "DATABASE_URL", "MIGRAR_AO_INICIAR", "DJANGO_SETTINGS_MODULE")
            }
            ambiente.update(variaveis)
            saida = subprocess.run(
                [sys.executable, "-c", "import rest.settings as s; print(s.MIGRAR_AO_INICIAR)"],
                cwd=settings.BASE_DIR, env=ambiente, capture_output=True, text=True, check=True,
            )
            return saida.stdout.strip()

        banco = "postgres://u:s@aws-0.pooler.supabase.com:6543/postgres"
        self.assertEqual(valor(VERCEL="1", DATABASE_URL=banco), "True")
        self.assertEqual(valor(VERCEL="1", CI="1", DATABASE_URL=banco), "False")   # build da Vercel
        self.assertEqual(valor(VERCEL="1"), "False")                               # sem banco de produção
        self.assertEqual(valor(DATABASE_URL=banco), "False")                       # fora da Vercel
        self.assertEqual(valor(VERCEL="1", DATABASE_URL=banco, MIGRAR_AO_INICIAR="0"), "False")
        self.assertEqual(valor(MIGRAR_AO_INICIAR="1"), "True")
