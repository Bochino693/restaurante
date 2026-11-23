"""Fotos no banco do site (core.midia), a rota /midia/banco/, o espelho da
Cloudinary e a restauração das fotos antigas (cópias locais e ZIP)."""
import io
import os
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
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from . import midia, restauracao
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
        self.assertRegex(nome, r"^banco/produtos/Coca Lata-[0-9a-f]{10}\.png$")
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
        self.assertEqual(ArquivoNoBanco.objects.count(), 1)

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

    def test_head_e_post(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_cache_em_disco_no_servidor_poupa_o_banco(self):
        with tempfile.TemporaryDirectory() as pasta, override_settings(MIDIA_CACHE_PASTA=pasta):
            self.client.get(self.url, {"w": 480})
            with self.assertNumQueries(1):
                resposta = self.client.get(self.url, {"w": 480})
            self.assertEqual(abrir(resposta.content).size, (480, 240))
            self.assertEqual(len(os.listdir(pasta)), 1)

    def test_na_vercel_sem_cache_em_disco(self):
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
