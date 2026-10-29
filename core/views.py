import hmac
import json
import logging
import re
import unicodedata
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import (
    Count, DecimalField, F, IntegerField, Max, Min, Prefetch, Q, Sum, Value,
)
from django.db.models.functions import Coalesce, ExtractHour, ExtractWeekDay, TruncDate
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from .context_processors import VERSAO_ESTATICOS
from .horario import status_da_loja
from .models import (
    CategoriaProdutos, EstoqueProdutos, ItensPedido, Pedidos, PratoDoDia,
    Produtos, ProdutosMaisClick,
)

logger = logging.getLogger(__name__)

DINHEIRO_14 = DecimalField(max_digits=14, decimal_places=2)
ZERO_REAIS = Value(Decimal("0.00"))

def _url_imagem(campo):
    """URL da imagem, ou "" quando o produto não tem foto (antes o cardápio
    inteiro dava erro 500 por causa de um único produto sem imagem)."""
    try:
        return campo.url if campo else ""
    except (ValueError, AttributeError):
        return ""


# Cor de cada categoria no efeito de "entrar no carrinho" (a bolha do +N e
# a faixa do item na lista): bebida é sempre azul; as outras categorias
# recebem as cores abaixo, na ordem em que foram criadas.
COR_BEBIDA = "#0284c7"
PALETA_CATEGORIAS = ["#f97316", "#16a34a", "#8b5cf6", "#e11d48", "#0d9488", "#ca8a04"]


def _cores_das_categorias():
    cores = {}
    proxima = 0
    for categoria in CategoriaProdutos.objects.order_by("id").only("id", "nome_categoria"):
        nome = categoria.nome_categoria.casefold()
        if any(palavra.casefold() in nome for palavra in settings.PALAVRAS_BEBIDAS):
            cores[categoria.id] = COR_BEBIDA
        else:
            cores[categoria.id] = PALETA_CATEGORIAS[proxima % len(PALETA_CATEGORIAS)]
            proxima += 1
    return cores


def _produto_para_cardapio(produto, cores=None):
    return {
        "id": produto.id,
        "nome": produto.nome_produto,
        "preco": f"{produto.preco:.2f}",
        "codigo": produto.codigo,
        "imagem": _url_imagem(produto.image_produto),
        "tem_adicionais": any(a.ativo for a in produto.adicionais_disponiveis.all()),
        # Bebida mostra a lata/garrafa inteira na foto (sem corte).
        "bebida": _eh_bebida(produto),
        "cor": (cores or {}).get(produto.categoria_id, PALETA_CATEGORIAS[0]),
    }


# O catálogo muda pouco durante o dia: fica 60 s na memória da função. Na
# Vercel isso poupa o banco a cada visita de cliente (a função quente
# reaproveita a memória entre requisições).
CARDAPIO_CACHE_SEGUNDOS = 60


def _dados_do_cardapio():
    dia_hoje = timezone.localdate().weekday()
    chave = f"cardapio:v4:{dia_hoje}"
    dados = cache.get(chave)
    if dados is not None:
        return dados

    produtos_ativos = (
        Produtos.objects
        .filter(ativo=True)
        .select_related("categoria")
        .order_by("nome_produto")
        .prefetch_related("adicionais_disponiveis")
    )
    categorias = (
        CategoriaProdutos.objects
        .filter(ativo=True, produtos__ativo=True)
        .distinct()
        .order_by("nome_categoria")
        .prefetch_related(Prefetch("produtos", queryset=produtos_ativos))
    )
    pratos = (
        PratoDoDia.objects
        .filter(dia_semana=dia_hoje, ativo=True, produto__ativo=True)
        .select_related("produto__categoria")
        .prefetch_related("produto__adicionais_disponiveis")
    )
    cores = _cores_das_categorias()
    dados = {
        "categorias": [
            {
                "id": categoria.id,
                "nome": categoria.nome_categoria,
                "produtos": [_produto_para_cardapio(p, cores) for p in categoria.produtos.all()],
            }
            for categoria in categorias
        ],
        "pratos_do_dia": [_produto_para_cardapio(prato.produto, cores) for prato in pratos],
    }
    dados["categorias"] = [c for c in dados["categorias"] if c["produtos"]]
    cache.set(chave, dados, CARDAPIO_CACHE_SEGUNDOS)
    return dados


def _limpar_cache_cardapio():
    cache.delete_many([f"cardapio:v4:{d}" for d in range(7)])


class CardapioClienteView(View):
    def get(self, request):
        dados = _dados_do_cardapio()
        loja = status_da_loja()
        catalogo = {
            str(p["id"]): p
            for p in dados["pratos_do_dia"] + [p for c in dados["categorias"] for p in c["produtos"]]
        }
        return render(request, "index.html", {
            **dados,
            "catalogo": catalogo,
            "loja": loja,
            "loja_aberta_server": loja["aberta"],
            "whatsapp_loja": settings.WHATSAPP_LOJA,
        })


@csrf_exempt
@require_POST
def registrar_clique(request, produto_id):
    """Conta o interesse do cliente num produto (o gráfico "mais clicados"
    do dashboard nunca recebia dado nenhum: não havia quem gravasse).

    Um registro por produto por dia; o incremento é feito no banco (F()),
    então dois cliques simultâneos não se perdem.
    """
    if not Produtos.objects.filter(id=produto_id, ativo=True).exists():
        return JsonResponse({"ok": False}, status=404)
    hoje = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    atualizados = ProdutosMaisClick.objects.filter(
        produto_id=produto_id, criacao__gte=hoje
    ).update(quantidade=F("quantidade") + 1)
    if not atualizados:
        ProdutosMaisClick.objects.create(produto_id=produto_id, quantidade=1)
    return JsonResponse({"ok": True})


class CaixaView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        produtos = (
            Produtos.objects
            .select_related("categoria")
            .prefetch_related("adicionais_disponiveis")
            .filter(ativo=True)
            .order_by("nome_produto")
        )

        categorias = (
            CategoriaProdutos.objects
            .filter(ativo=True, produtos__ativo=True)
            .distinct()
            .order_by("nome_categoria")
        )

        dia_hoje = timezone.localdate().weekday()
        pratos_do_dia = (
            PratoDoDia.objects
            .filter(
                dia_semana=dia_hoje,
                ativo=True,
                produto__ativo=True
            )
            .select_related("produto", "produto__categoria")
            .prefetch_related("produto__adicionais_disponiveis")
            .order_by("produto__nome_produto")
        )

        # Bebida mostra a lata/garrafa inteira na foto (sem corte) e cada
        # categoria tem a sua cor no efeito de entrar no carrinho.
        cores = _cores_das_categorias()
        for produto in list(produtos) + [prato.produto for prato in pratos_do_dia]:
            produto.eh_bebida = _eh_bebida(produto)
            produto.cor = cores.get(produto.categoria_id, PALETA_CATEGORIAS[0])

        return render(request, 'caixa.html', {
            'produtos': produtos,
            'categorias': categorias,
            'pratos_do_dia': pratos_do_dia,
        })

    def post(self, request):
        centavos = Decimal("0.01")

        def resposta_erro(mensagem, status=400):
            return JsonResponse(
                {"success": False, "message": mensagem},
                status=status
            )

        def decimal_seguro(valor, campo):
            try:
                numero = Decimal(str(valor if valor not in (None, "") else 0))
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError(f"{campo} inválido.")

            if not numero.is_finite():
                raise ValueError(f"{campo} inválido.")

            return numero.quantize(centavos, rounding=ROUND_HALF_UP)

        try:
            try:
                data = json.loads(request.body or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return resposta_erro("JSON inválido.")

            if not isinstance(data, dict):
                return resposta_erro("Dados do pedido inválidos.")

            carrinho = data.get("carrinho") or []
            nome_cliente = str(data.get("nome_cliente") or "").strip()
            descricao = str(data.get("descricao") or "").strip()
            metodo_pagamento = str(
                data.get("metodo_pagamento") or ""
            ).strip().upper()
            tipo_entrega = str(
                data.get("tipo_entrega") or ""
            ).strip().lower()
            endereco = data.get("endereco") or {}

            if not isinstance(carrinho, list) or not carrinho:
                return resposta_erro("Carrinho vazio.")

            if not nome_cliente:
                return resposta_erro("Nome do cliente é obrigatório.")

            if len(nome_cliente) > 90:
                return resposta_erro(
                    "O nome do cliente deve ter no máximo 90 caracteres."
                )

            pagamentos_validos = {
                valor for valor, _ in Pedidos.FormaPagamento.choices
            }
            if metodo_pagamento not in pagamentos_validos:
                return resposta_erro("Método de pagamento inválido.")

            if tipo_entrega not in {"entrega", "retirada"}:
                return resposta_erro("Tipo de entrega inválido.")

            entrega = tipo_entrega == "entrega"

            if not isinstance(endereco, dict):
                return resposta_erro("Endereço inválido.")

            cep = str(endereco.get("cep") or "").strip()
            rua = str(endereco.get("rua") or "").strip()
            numero = str(endereco.get("numero") or "").strip()
            telefone = str(endereco.get("telefone") or "").strip()

            if entrega and (not rua or not numero):
                return resposta_erro(
                    "Rua e número são obrigatórios para entrega."
                )

            if len(cep) > 9 or len(rua) > 120 or len(numero) > 50 or len(telefone) > 20:
                return resposta_erro(
                    "O endereço ultrapassa o tamanho permitido."
                )

            desconto = decimal_seguro(
                data.get("desconto", 0),
                "Desconto"
            )
            taxa_motoca = (
                decimal_seguro(
                    data.get("taxa_motoca", 0),
                    "Taxa de entrega"
                )
                if entrega
                else Decimal("0.00")
            )

            if desconto < 0 or taxa_motoca < 0:
                return resposta_erro(
                    "Desconto e taxa não podem ser negativos."
                )

            itens_preparados = []
            total_bruto = Decimal("0.00")

            # Valida todos os itens antes de iniciar a transação.
            for posicao, item in enumerate(carrinho, start=1):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"Item {posicao} do carrinho é inválido."
                    )

                produto_id = item.get("id")
                if produto_id in (None, ""):
                    raise ValueError(
                        f"Produto ausente no item {posicao}."
                    )

                try:
                    quantidade = int(item.get("qtd", 0))
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Quantidade inválida no item {posicao}."
                    )

                if quantidade <= 0:
                    raise ValueError(
                        f"Quantidade deve ser maior que zero "
                        f"no item {posicao}."
                    )

                produto = (
                    Produtos.objects
                    .prefetch_related("adicionais_disponiveis")
                    .filter(id=produto_id, ativo=True)
                    .first()
                )

                if not produto:
                    raise Produtos.DoesNotExist

                adicionais_recebidos = item.get("adicionais") or []
                if not isinstance(adicionais_recebidos, list):
                    raise ValueError(
                        f"Adicionais inválidos no produto "
                        f"{produto.nome_produto}."
                    )

                adicionais_disponiveis = {
                    adicional.nome.strip().casefold(): adicional
                    for adicional
                    in produto.adicionais_disponiveis.all()
                    if adicional.ativo
                }

                adicionais_normalizados = []
                soma_adicionais = Decimal("0.00")

                for adicional_recebido in adicionais_recebidos:
                    if not isinstance(adicional_recebido, dict):
                        raise ValueError(
                            f"Adicional inválido no produto "
                            f"{produto.nome_produto}."
                        )

                    nome_recebido = str(
                        adicional_recebido.get("nome") or ""
                    ).strip()

                    if not nome_recebido:
                        continue

                    adicional_banco = adicionais_disponiveis.get(
                        nome_recebido.casefold()
                    )

                    if not adicional_banco:
                        raise ValueError(
                            f'O adicional "{nome_recebido}" não está '
                            f'disponível para {produto.nome_produto}.'
                        )

                    try:
                        qtd_adicional = int(
                            adicional_recebido.get("qtd", 1)
                        )
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"Quantidade de adicional inválida em "
                            f"{produto.nome_produto}."
                        )

                    if qtd_adicional <= 0:
                        continue

                    preco_adicional = Decimal(
                        str(adicional_banco.preco)
                    ).quantize(
                        centavos,
                        rounding=ROUND_HALF_UP
                    )

                    soma_adicionais += (
                        preco_adicional * qtd_adicional
                    )

                    adicionais_normalizados.append({
                        "nome": adicional_banco.nome,
                        "qtd": qtd_adicional,
                        "preco": str(preco_adicional)
                    })

                preco_base = Decimal(
                    str(produto.preco)
                ).quantize(
                    centavos,
                    rounding=ROUND_HALF_UP
                )

                preco_unitario = (
                    preco_base + soma_adicionais
                ).quantize(
                    centavos,
                    rounding=ROUND_HALF_UP
                )

                subtotal_item = (
                    preco_unitario * quantidade
                ).quantize(
                    centavos,
                    rounding=ROUND_HALF_UP
                )

                total_bruto += subtotal_item

                itens_preparados.append({
                    "produto": produto,
                    "quantidade": quantidade,
                    "preco_base": preco_base,
                    "preco_unitario": preco_unitario,
                    "subtotal": subtotal_item,
                    "adicionais": adicionais_normalizados,
                })

            if not itens_preparados:
                return resposta_erro(
                    "O pedido não possui itens válidos."
                )

            desconto = min(desconto, total_bruto)
            total_pedido = (
                total_bruto - desconto
            ).quantize(
                centavos,
                rounding=ROUND_HALF_UP
            )

            with transaction.atomic():
                pedido = Pedidos.objects.create(
                    nome_cliente=nome_cliente,
                    descricao=descricao or None,
                    total=total_pedido,
                    taxa_motoca=taxa_motoca,
                    forma_pagamento=metodo_pagamento,
                    entrega=entrega,
                    cep=cep or None,
                    rua=rua or None,
                    numero=numero or None,
                    telefone=telefone or None,
                    impresso=False
                )

                itens_salvos = []
                itens_impressao = []

                for item in itens_preparados:
                    item_pedido = ItensPedido.objects.create(
                        produto=item["produto"],
                        quantidade=item["quantidade"],
                        preco_unitario=item["preco_unitario"],
                        subtotal=item["subtotal"],
                        adicionais=item["adicionais"]
                    )
                    itens_salvos.append(item_pedido)

                    itens_impressao.append({
                        "id": item_pedido.id,
                        "produtoId": item["produto"].id,
                        "nome": item["produto"].nome_produto,
                        "qtd": item["quantidade"],
                        "precoBase": float(item["preco_base"]),
                        "precoUnitario": float(
                            item["preco_unitario"]
                        ),
                        "subtotal": float(item["subtotal"]),
                        "adicionais": [
                            {
                                "nome": adicional["nome"],
                                "qtd": adicional["qtd"],
                                "preco": float(
                                    Decimal(adicional["preco"])
                                )
                            }
                            for adicional in item["adicionais"]
                        ]
                    })

                # Grava diretamente na tabela intermediária.
                # Diferente de pedido.itens.add(), bulk_create não ignora
                # silenciosamente uma restrição incorreta no banco.
                campo_itens = Pedidos._meta.get_field("itens")
                modelo_relacao = campo_itens.remote_field.through

                campos_fk = [
                    campo
                    for campo in modelo_relacao._meta.fields
                    if getattr(campo, "remote_field", None)
                ]

                campo_pedido = next(
                    (
                        campo
                        for campo in campos_fk
                        if (
                            campo.remote_field.model._meta.label_lower
                            == Pedidos._meta.label_lower
                        )
                    ),
                    None
                )
                campo_item = next(
                    (
                        campo
                        for campo in campos_fk
                        if (
                            campo.remote_field.model._meta.label_lower
                            == ItensPedido._meta.label_lower
                        )
                    ),
                    None
                )

                if not campo_pedido or not campo_item:
                    raise DatabaseError(
                        "Não foi possível identificar a relação "
                        "entre pedido e itens."
                    )

                relacoes = [
                    modelo_relacao(**{
                        campo_pedido.name: pedido,
                        campo_item.name: item_pedido
                    })
                    for item_pedido in itens_salvos
                ]

                modelo_relacao._default_manager.bulk_create(
                    relacoes,
                    batch_size=100
                )

                ids_esperados = {
                    item_pedido.pk for item_pedido in itens_salvos
                }
                ids_relacionados = set(
                    modelo_relacao._default_manager
                    .filter(**{campo_pedido.name: pedido})
                    .values_list(campo_item.attname, flat=True)
                )

                if ids_relacionados != ids_esperados:
                    logger.error(
                        "Relação incompleta no pedido %s. "
                        "Esperados=%s; relacionados=%s",
                        pedido.pk,
                        sorted(ids_esperados),
                        sorted(ids_relacionados)
                    )
                    raise DatabaseError(
                        "A tabela de relação entre pedidos e itens "
                        "está com uma restrição incorreta. Aplique a "
                        "migration 0011_reparar_relacao_pedidos_itens."
                    )

                quantidade_unidades = sum(
                    item["quantidade"]
                    for item in itens_preparados
                )

                pedido_impressao = {
                    "id": pedido.id,
                    "criadoEm": timezone.localtime(
                        pedido.criado_em
                    ).strftime("%d/%m/%Y %H:%M"),
                    "nomeCliente": pedido.nome_cliente,
                    "metodo": pedido.forma_pagamento,
                    "entrega": pedido.entrega,
                    "descricao": pedido.descricao or "",
                    "endereco": {
                        "cep": pedido.cep or "",
                        "rua": pedido.rua or "",
                        "numero": pedido.numero or "",
                        "telefone": pedido.telefone or ""
                    },
                    "itens": itens_impressao,
                    "quantidadeLinhas": len(itens_impressao),
                    "quantidadeUnidades": quantidade_unidades,
                    "desconto": float(desconto),
                    "totalBruto": float(total_bruto),
                    "totalPedido": float(pedido.total),
                    "taxaMotoca": float(pedido.taxa_motoca),
                    "totalFinal": float(
                        pedido.total + pedido.taxa_motoca
                    )
                }

            return JsonResponse({
                "success": True,
                "pedido_id": pedido.id,
                "pedido_impressao": pedido_impressao
            })

        except Produtos.DoesNotExist:
            return resposta_erro(
                "Um dos produtos não existe ou está inativo.",
                status=404
            )

        except (KeyError, ValueError, InvalidOperation) as erro:
            return resposta_erro(str(erro), status=400)

        except DatabaseError as erro:
            logger.exception(
                "Erro de banco ao finalizar pedido no PDV"
            )
            mensagem = str(erro)

            if "0011_reparar_relacao_pedidos_itens" in mensagem:
                return resposta_erro(mensagem, status=500)

            return resposta_erro(
                "Falha ao gravar o pedido no banco. "
                "Aplique todas as migrations e tente novamente.",
                status=500
            )

        except Exception:
            logger.exception(
                "Erro inesperado ao finalizar pedido no PDV"
            )
            return resposta_erro(
                "Erro interno ao finalizar o pedido. "
                "Consulte os logs do servidor.",
                status=500
            )




def _chave_da_impressora_ok(request):
    chave = request.headers.get("X-API-Key") or ""
    return bool(chave) and hmac.compare_digest(chave, settings.IMPRESSAO_API_KEY)


@csrf_exempt
@require_POST
def confirmar_impressao(request, pedido_id):
    """Marca o pedido como impresso somente após confirmação do cliente de impressão.

    Aceita a sessão autenticada do PDV ou a chave usada pelo agente externo.
    A operação é idempotente: confirmar novamente um pedido já impresso continua
    retornando sucesso, evitando falsos erros após reconexões do QZ Tray.
    """
    autorizado_por_sessao = bool(
        getattr(request, "user", None)
        and request.user.is_authenticated
    )
    autorizado_por_chave = _chave_da_impressora_ok(request)

    if not (autorizado_por_sessao or autorizado_por_chave):
        return JsonResponse({"ok": False, "erro": "Não autorizado"}, status=401)

    pedido = Pedidos.objects.filter(id=pedido_id).only("id", "impresso").first()
    if not pedido:
        return JsonResponse({"ok": False, "erro": "Pedido não encontrado"}, status=404)

    ja_impresso = pedido.impresso
    if not ja_impresso:
        Pedidos.objects.filter(id=pedido_id, impresso=False).update(impresso=True)

    return JsonResponse({
        "ok": True,
        "pedido_id": pedido_id,
        "ja_impresso": ja_impresso,
    })


def pedidos_pendentes_impressao(request):
    """Retorna pedidos com impresso=False — NÃO marca como impresso aqui"""
    if not _chave_da_impressora_ok(request):
        return JsonResponse({'erro': 'Não autorizado'}, status=401)

    if request.method != 'GET':
        return JsonResponse({'erro': 'Método inválido'}, status=405)

    pedidos = (
        Pedidos.objects
        .filter(impresso=False)
        .prefetch_related('itens__produto')
        .order_by('criado_em')
    )

    resultado = []
    for pedido in pedidos:
        itens = []
        for item in pedido.itens.all():
            itens.append({
                'nome': item.produto.nome_produto if item.produto else 'Produto removido',
                'quantidade': item.quantidade,
                'preco_unitario': str(item.preco_unitario),
                'subtotal': str(item.subtotal),
                'adicionais': item.adicionais or []
            })

        resultado.append({
            'id': pedido.id,
            'nome_cliente': pedido.nome_cliente,
            'forma_pagamento': pedido.forma_pagamento,
            'entrega': pedido.entrega,
            'rua': pedido.rua or '',
            'numero': pedido.numero or '',
            'cep': pedido.cep or '',
            'telefone': pedido.telefone or '',
            'total': str(pedido.total),
            'taxa_motoca': str(pedido.taxa_motoca),
            'criado_em': timezone.localtime(pedido.criado_em).strftime('%d/%m/%Y %H:%M'),
            'descricao': pedido.descricao or '',
            'itens': itens
        })

    return JsonResponse({'pedidos': resultado})


# Adicional que é bebida (o refri de R$ 2 das marmitas) abre a lista de
# adicionais no Caixa e no cardápio, em destaque, com foto e na cor das
# bebidas. É bebida o nome com uma das PALAVRAS_BEBIDAS ou de um refri.
PALAVRAS_REFRI = ("coca", "guarana", "fanta", "sprite", "pepsi", "kuat", "soda", "tubaina")
FOTO_REFRI_PADRAO = "core/img/refri.svg"


def _sem_acento(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if not unicodedata.combining(c)
    ).casefold()


def _adicional_eh_bebida(nome):
    nome = _sem_acento(nome)
    return any(_sem_acento(palavra) in nome for palavra in (*settings.PALAVRAS_BEBIDAS, *PALAVRAS_REFRI))


def _fotos_dos_adicionais(adicionais):
    """Foto de cada adicional: a do produto de mesmo nome (o "Coca-Cola Lata"
    das bebidas empresta a foto ao adicional "Coca-Cola Lata"), mesmo que o
    produto esteja desativado no cardápio. Sem foto: "" (o chamador decide)."""
    nomes = {adicional.nome.strip() for adicional in adicionais if adicional.nome.strip()}
    if not nomes:
        return {}
    filtro = Q()
    for nome in nomes:
        filtro |= Q(nome_produto__iexact=nome)
    fotos = {}
    for produto in Produtos.objects.filter(filtro).exclude(image_produto="").order_by("-ativo", "id"):
        url = _url_imagem(produto.image_produto)
        if url:
            fotos.setdefault(produto.nome_produto.strip().casefold(), url)
    return fotos


def adicionais_produto(request, produto_id):
    produto = get_object_or_404(Produtos, id=produto_id)
    adicionais = list(produto.adicionais_disponiveis.filter(ativo=True).order_by("id"))

    # Bebida primeiro; os outros na ordem em que foram cadastrados.
    bebidas = {adicional.id for adicional in adicionais if _adicional_eh_bebida(adicional.nome)}
    adicionais.sort(key=lambda adicional: adicional.id not in bebidas)
    fotos = _fotos_dos_adicionais(adicionais)
    foto_refri = f"{static(FOTO_REFRI_PADRAO)}?v={VERSAO_ESTATICOS}"

    data = []
    for adicional in adicionais:
        bebida = adicional.id in bebidas
        foto = fotos.get(adicional.nome.strip().casefold(), "")
        data.append({
            "nome": adicional.nome,
            "preco": str(adicional.preco),
            "bebida": bebida,
            "imagem": foto or (foto_refri if bebida else ""),
            "cor": COR_BEBIDA if bebida else "",
        })

    return JsonResponse(data, safe=False)


class EstoqueView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        estoque = EstoqueProdutos.objects.select_related(
            'produtos',
            'produtos__categoria'
        ).all()

        categorias = CategoriaProdutos.objects.all()

        return render(request, 'estoque.html', {
            'estoque': estoque,
            'categorias': categorias
        })





def _range_periodo(periodo):
    hoje = timezone.localdate()

    if periodo == "hoje":
        inicio = hoje
        fim = hoje + timedelta(days=1)
        label = "Total Hoje"

    elif periodo == "semana":
        inicio = hoje - timedelta(days=6)
        fim = hoje + timedelta(days=1)
        label = "Últimos 7 dias"

    elif periodo == "quinzena":
        inicio = hoje - timedelta(days=14)
        fim = hoje + timedelta(days=1)
        label = "Últimos 15 dias"

    elif periodo == "mes":
        inicio = hoje.replace(day=1)

        if inicio.month == 12:
            fim = inicio.replace(year=inicio.year + 1, month=1, day=1)
        else:
            fim = inicio.replace(month=inicio.month + 1, day=1)

        label = "Este mês"

    else:
        inicio = None
        fim = None
        label = "Total Geral"

    if inicio and fim:
        inicio_dt = timezone.make_aware(datetime.combine(inicio, time.min))
        fim_dt = timezone.make_aware(datetime.combine(fim, time.min))
        return inicio_dt, fim_dt, label

    return None, None, label




def _filtrar_pedidos(request):
    periodo = request.GET.get("periodo", "hoje")
    pagamentos = request.GET.getlist("pagamento")

    pedidos = (
        Pedidos.objects
        .all()
        .prefetch_related("itens", "itens__produto", "itens__produto__categoria")
        .order_by("-criado_em")
    )

    inicio_dt, fim_dt, label = _range_periodo(periodo)

    if inicio_dt and fim_dt:
        pedidos = pedidos.filter(
            criado_em__gte=inicio_dt,
            criado_em__lt=fim_dt
        )
    else:
        periodo = "todos"

    pagamentos_validos = [
        Pedidos.FormaPagamento.DINHEIRO,
        Pedidos.FormaPagamento.PIX,
        Pedidos.FormaPagamento.CARTAO,
        Pedidos.FormaPagamento.MISTO,
    ]

    pagamentos = [p for p in pagamentos if p in pagamentos_validos]

    if pagamentos:
        pedidos = pedidos.filter(forma_pagamento__in=pagamentos)

    return pedidos, periodo, pagamentos, label



def _formatar_moeda(valor):
    valor = valor or Decimal("0.00")
    return f"{valor:.2f}".replace(".", ",")


def _q_bebidas(campo="produto__categoria__nome_categoria"):
    """Filtro dos itens que são bebida: a categoria do produto tem no nome
    uma das PALAVRAS_BEBIDAS (settings). Sem palavras, nada é bebida."""
    filtro = Q()
    for palavra in settings.PALAVRAS_BEBIDAS:
        filtro |= Q(**{f"{campo}__icontains": palavra})
    return filtro or Q(pk__in=[])


def _ranking_bebidas(itens):
    """Bebidas vendidas, da mais pedida para a menos: uma consulta só."""
    return list(
        itens
        .filter(_q_bebidas())
        .values("produto__nome_produto")
        .annotate(
            unidades=Coalesce(Sum("quantidade"), Value(0), output_field=IntegerField()),
            receita=Coalesce(Sum("subtotal"), ZERO_REAIS, output_field=DINHEIRO_14),
        )
        .order_by("-unidades", "-receita", "produto__nome_produto")
    )


def _resumo_bebidas(pedidos):
    """Relatório de bebidas dos pedidos (já sem os cancelados).

    O valor é a soma dos itens de bebida (preço x quantidade), igual ao que
    aparece em cada pedido; o desconto do pedido inteiro não é rateado.
    """
    ranking = _ranking_bebidas(ItensPedido.objects.filter(pedidos__in=pedidos))
    return {
        "receita": sum((item["receita"] for item in ranking), Decimal("0.00")),
        "unidades": sum(item["unidades"] for item in ranking),
        "lista": [
            {
                "nome": item["produto__nome_produto"] or "Produto removido",
                "unidades": item["unidades"],
                "receita": _formatar_moeda(item["receita"]),
            }
            for item in ranking
        ],
    }


def _montar_range_paginacao(page_obj, janela=2):
    paginator = page_obj.paginator
    pagina_atual = page_obj.number
    total_paginas = paginator.num_pages

    if total_paginas <= 7:
        return list(range(1, total_paginas + 1))

    paginas = {1, total_paginas}

    for numero in range(pagina_atual - janela, pagina_atual + janela + 1):
        if 1 <= numero <= total_paginas:
            paginas.add(numero)

    resultado = []
    anterior = None

    for numero in sorted(paginas):
        if anterior is not None and numero - anterior > 1:
            resultado.append("...")
        resultado.append(numero)
        anterior = numero

    return resultado





def _agregar_totais_pedidos(pedidos):
    """
    Calcula os totais gerais e a conferência do caixa físico.

    Regras do caixa em dinheiro:
    - retirada: valor do pedido;
    - entrega: valor do pedido + taxa do motoca;
    - cancelados devem ser excluídos antes de chamar esta função.
    """
    totais = pedidos.aggregate(
        soma_total=Sum("total"),
        soma_taxa=Sum("taxa_motoca"),

        # Quantos pedidos formam cada valor (aparece embaixo de cada cartão).
        qtd_total=Count("id"),
        qtd_dinheiro=Count("id", filter=Q(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO)),
        qtd_pix=Count("id", filter=Q(forma_pagamento=Pedidos.FormaPagamento.PIX)),
        qtd_cartao=Count("id", filter=Q(forma_pagamento=Pedidos.FormaPagamento.CARTAO)),
        qtd_misto=Count("id", filter=Q(forma_pagamento=Pedidos.FormaPagamento.MISTO)),
        qtd_entregas=Count("id", filter=Q(entrega=True)),
        qtd_dinheiro_retirada=Count(
            "id", filter=Q(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO, entrega=False)
        ),
        qtd_dinheiro_entrega=Count(
            "id", filter=Q(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO, entrega=True)
        ),

        soma_dinheiro=Sum(
            "total",
            filter=Q(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO),
        ),
        soma_pix=Sum(
            "total",
            filter=Q(forma_pagamento=Pedidos.FormaPagamento.PIX),
        ),
        soma_cartao=Sum(
            "total",
            filter=Q(forma_pagamento=Pedidos.FormaPagamento.CARTAO),
        ),
        soma_misto=Sum(
            "total",
            filter=Q(forma_pagamento=Pedidos.FormaPagamento.MISTO),
        ),

        # Dinheiro recebido em pedidos de retirada.
        soma_dinheiro_retirada=Sum(
            "total",
            filter=Q(
                forma_pagamento=Pedidos.FormaPagamento.DINHEIRO,
                entrega=False,
            ),
        ),

        # Parte do pedido recebida em dinheiro nas entregas.
        soma_dinheiro_entrega_pedidos=Sum(
            "total",
            filter=Q(
                forma_pagamento=Pedidos.FormaPagamento.DINHEIRO,
                entrega=True,
            ),
        ),

        # A taxa também entra no caixa quando a entrega foi paga em dinheiro.
        soma_dinheiro_entrega_taxas=Sum(
            "taxa_motoca",
            filter=Q(
                forma_pagamento=Pedidos.FormaPagamento.DINHEIRO,
                entrega=True,
            ),
        ),
    )

    zero = Decimal("0.00")

    dinheiro_retirada = totais["soma_dinheiro_retirada"] or zero
    dinheiro_entrega = (
        (totais["soma_dinheiro_entrega_pedidos"] or zero)
        + (totais["soma_dinheiro_entrega_taxas"] or zero)
    )

    totais["dinheiro_retirada"] = dinheiro_retirada
    totais["dinheiro_entrega"] = dinheiro_entrega
    totais["dinheiro_caixa"] = dinheiro_retirada + dinheiro_entrega

    return totais


class PedidosView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        pedidos_filtrados, periodo, pagamentos, label = _filtrar_pedidos(request)

        pedidos_totais = pedidos_filtrados.exclude(
            status=Pedidos.StatusPedido.CANCELADO
        )

        totais = _agregar_totais_pedidos(pedidos_totais)
        bebidas = _resumo_bebidas(pedidos_totais)

        por_pagina = 12
        paginator = Paginator(pedidos_filtrados, por_pagina)

        pagina_atual = request.GET.get("page", 1)
        page_obj = paginator.get_page(pagina_atual)

        query_params = request.GET.copy()
        query_params.pop("page", None)
        query_string = query_params.urlencode()

        return render(request, "pedidos.html", {
            "pedidos": page_obj,
            "page_obj": page_obj,
            "paginator": paginator,
            "paginas_range": _montar_range_paginacao(page_obj),
            "query_string": query_string,

            "periodo_atual": periodo,
            "pagamentos_atuais": pagamentos,

            "label_total": label,
            "total_dia": _reais(totais["soma_total"]),
            "total_dinheiro": _reais(totais["soma_dinheiro"]),
            "total_pix": _reais(totais["soma_pix"]),
            "total_cartao": _reais(totais["soma_cartao"]),
            "total_misto": _reais(totais["soma_misto"]),
            "total_taxa_motoca": _reais(totais["soma_taxa"]),

            # Quantos pedidos formam cada cartão.
            "qtd_total": totais["qtd_total"],
            "qtd_dinheiro": totais["qtd_dinheiro"],
            "qtd_pix": totais["qtd_pix"],
            "qtd_cartao": totais["qtd_cartao"],
            "qtd_misto": totais["qtd_misto"],
            "qtd_entregas": totais["qtd_entregas"],
            "qtd_dinheiro_retirada": totais["qtd_dinheiro_retirada"],
            "qtd_dinheiro_entrega": totais["qtd_dinheiro_entrega"],

            # Relatório de bebidas: valor, unidades e cada bebida vendida.
            "total_bebidas": _reais(bebidas["receita"]),
            "qtd_bebidas": bebidas["unidades"],
            "bebidas_vendidas": bebidas["lista"],

            # Novos valores para conferência do caixa físico.
            "dinheiro_retirada": _reais(totais["dinheiro_retirada"]),
            "dinheiro_entrega": _reais(totais["dinheiro_entrega"]),
            "dinheiro_caixa": _reais(totais["dinheiro_caixa"]),

            # Os pedidos desta página, prontos para o modal de detalhes abrir
            # na hora (sem ir ao servidor).
            "pedidos_detalhes": {str(p.id): _detalhe_pedido(p) for p in page_obj},

            "status_options": Pedidos.StatusPedido.choices,
            "pagamento_options": Pedidos.FormaPagamento.choices,
        })


class ResumoPedidosView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        pedidos, periodo, pagamentos, label = _filtrar_pedidos(request)

        pedidos_totais = pedidos.exclude(
            status=Pedidos.StatusPedido.CANCELADO
        )

        totais = _agregar_totais_pedidos(pedidos_totais)
        bebidas = _resumo_bebidas(pedidos_totais)

        return JsonResponse({
            "label": label,
            "total_dia": _formatar_moeda(totais["soma_total"]),
            "total_taxa": _formatar_moeda(totais["soma_taxa"]),
            "total_dinheiro": _formatar_moeda(totais["soma_dinheiro"]),
            "total_pix": _formatar_moeda(totais["soma_pix"]),
            "total_cartao": _formatar_moeda(totais["soma_cartao"]),
            "total_misto": _formatar_moeda(totais["soma_misto"]),
            "total_bebidas": _formatar_moeda(bebidas["receita"]),
            "qtd_bebidas": bebidas["unidades"],
            "bebidas_vendidas": bebidas["lista"],

            # Novos valores, também obedecendo aos filtros atuais.
            "dinheiro_retirada": _formatar_moeda(totais["dinheiro_retirada"]),
            "dinheiro_entrega": _formatar_moeda(totais["dinheiro_entrega"]),
            "dinheiro_caixa": _formatar_moeda(totais["dinheiro_caixa"]),
        })


def _eh_bebida(produto):
    """Mesma regra do relatório (_q_bebidas), para um produto já carregado."""
    categoria = getattr(produto, "categoria", None) if produto else None
    nome = (categoria.nome_categoria if categoria else "").casefold()
    return any(palavra.casefold() in nome for palavra in settings.PALAVRAS_BEBIDAS)


def _link_whatsapp(telefone):
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    return f"https://wa.me/{digitos}" if len(digitos) >= 12 else ""


ETAPAS_PEDIDO = [
    Pedidos.StatusPedido.PAGO,
    Pedidos.StatusPedido.PREPARO,
    Pedidos.StatusPedido.ENTREGA,
    Pedidos.StatusPedido.FINALIZADO,
]


def _detalhe_pedido(pedido):
    """Tudo do pedido para o modal de detalhes (usa os itens já buscados)."""
    itens = []
    bruto = Decimal("0.00")
    unidades = 0
    for item in pedido.itens.all():
        adicionais = []
        for adicional in item.adicionais or []:
            if not isinstance(adicional, dict) or not str(adicional.get("nome") or "").strip():
                continue
            try:
                preco = _reais(Decimal(str(adicional.get("preco") or 0)))
            except (InvalidOperation, TypeError, ValueError):
                preco = ""
            adicionais.append({
                "nome": str(adicional.get("nome")).strip(),
                "qtd": adicional.get("qtd", 1),
                "preco": preco,
            })
        itens.append({
            "nome": item.produto.nome_produto if item.produto else "Produto removido",
            "qtd": item.quantidade,
            "unitario": _reais(item.preco_unitario),
            "subtotal": _reais(item.subtotal),
            "adicionais": adicionais,
            "bebida": _eh_bebida(item.produto),
        })
        bruto += item.subtotal or Decimal("0.00")
        unidades += item.quantidade or 0

    total = pedido.total or Decimal("0.00")
    taxa = pedido.taxa_motoca or Decimal("0.00")
    desconto = bruto - total if bruto > total else Decimal("0.00")
    criado = timezone.localtime(pedido.criado_em)
    endereco = ", ".join(parte for parte in [pedido.rua or "", pedido.numero or ""] if parte)
    proximo = pedido.proximo_status()

    return {
        "id": pedido.id,
        "status": pedido.status,
        "status_label": pedido.get_status_display(),
        "etapa": ETAPAS_PEDIDO.index(pedido.status) if pedido.status in ETAPAS_PEDIDO else -1,
        "proximo": proximo or "",
        "proximo_label": Pedidos.StatusPedido(proximo).label if proximo else "",
        "pagamento": pedido.forma_pagamento,
        "pagamento_label": pedido.get_forma_pagamento_display(),
        "data": criado.strftime("%d/%m/%Y"),
        "hora": criado.strftime("%H:%M"),
        "criado_iso": criado.isoformat(),
        "cliente": pedido.nome_cliente or "",
        "telefone": pedido.telefone or "",
        "whatsapp": _link_whatsapp(pedido.telefone),
        "entrega": pedido.entrega,
        "endereco": endereco,
        "cep": pedido.cep or "",
        "mapa": (
            "https://www.google.com/maps/search/?api=1&query="
            + quote(" ".join(parte for parte in [endereco, pedido.cep or ""] if parte))
        ) if pedido.entrega and endereco else "",
        "descricao": pedido.descricao or "",
        "impresso": pedido.impresso,
        "itens": itens,
        "unidades": unidades,
        "bruto": _reais(bruto),
        "desconto": _reais(desconto) if desconto > 0 else "",
        "total": _reais(total),
        "taxa": _reais(taxa) if taxa > 0 else "",
        "total_final": _reais(total + taxa),
    }


class PedidoDetalheView(LoginRequiredMixin, View):
    """Detalhes de um pedido para o modal (pedidos de fora da página atual,
    abertos a partir das listas dos relatórios)."""
    login_url = "login"

    def get(self, request, pedido_id):
        pedido = get_object_or_404(
            Pedidos.objects.prefetch_related("itens__produto__categoria"), id=pedido_id
        )
        return JsonResponse(_detalhe_pedido(pedido))


class RelatorioPedidosView(LoginRequiredMixin, View):
    """O que está por trás de cada cartão do topo da tela de Pedidos: o valor,
    os destaques, a divisão e a lista dos pedidos que formam aquele número.
    Usa os mesmos filtros da tela (período e pagamento) e nunca os cancelados."""
    login_url = "login"
    LIMITE = 200

    PAGAMENTOS = {
        "dinheiro": (Pedidos.FormaPagamento.DINHEIRO, "Dinheiro", "fa-money-bill-wave"),
        "pix": (Pedidos.FormaPagamento.PIX, "Pix", "fa-brands fa-pix"),
        "cartao": (Pedidos.FormaPagamento.CARTAO, "Cartão", "fa-credit-card"),
        "misto": (Pedidos.FormaPagamento.MISTO, "Misto", "fa-shuffle"),
    }
    TONS = {
        Pedidos.FormaPagamento.DINHEIRO: "dinheiro",
        Pedidos.FormaPagamento.PIX: "pix",
        Pedidos.FormaPagamento.CARTAO: "cartao",
        Pedidos.FormaPagamento.MISTO: "misto",
    }

    def get(self, request, tipo):
        pedidos, _periodo, _pagamentos, label = _filtrar_pedidos(request)
        pedidos = pedidos.prefetch_related(None)
        validos = pedidos.exclude(status=Pedidos.StatusPedido.CANCELADO)
        totais = _agregar_totais_pedidos(validos)

        if tipo in self.PAGAMENTOS:
            dados = self._pagamento(validos, totais, *self.PAGAMENTOS[tipo])
        elif tipo in ("total", "taxa", "bebidas", "retirada_dinheiro", "entrega_dinheiro", "caixa_dinheiro"):
            dados = getattr(self, f"_{tipo}")(pedidos, validos, totais)
        else:
            return JsonResponse({"erro": "Relatório não encontrado."}, status=404)

        dados.update({"tipo": tipo, "periodo": label, "limite": self.LIMITE})
        return JsonResponse(dados)

    # ---------------------------------------------------------- utilitários
    def _lista(self, selecao, valor=lambda p: p.total, detalhe=None):
        linhas = []
        for pedido in selecao[:self.LIMITE]:
            criado = timezone.localtime(pedido.criado_em)
            linhas.append({
                "id": pedido.id,
                "hora": criado.strftime("%H:%M"),
                "data": criado.strftime("%d/%m"),
                "cliente": pedido.nome_cliente or "Sem nome",
                "valor": _reais(valor(pedido)),
                "status": pedido.status,
                "status_label": pedido.get_status_display(),
                "detalhe": detalhe(pedido) if detalhe else (
                    ("Entrega" if pedido.entrega else "Retirada") + " · " + pedido.get_forma_pagamento_display()
                ),
            })
        return {"pedidos": linhas, "pedidos_total": selecao.count()}

    @staticmethod
    def _quebra(partes):
        """[(rótulo, valor, tom, qtd)] -> barras com a porcentagem de cada parte."""
        soma = sum((valor or Decimal("0.00") for _, valor, _, _ in partes), Decimal("0.00"))
        return [
            {
                "rotulo": rotulo,
                "valor": _reais(valor),
                "pct": round(float((valor or 0) / soma * 100), 1) if soma else 0,
                "tom": tom,
                "qtd": qtd,
            }
            for rotulo, valor, tom, qtd in partes
        ]

    @staticmethod
    def _media(valor, qtd):
        return _reais((valor or Decimal("0.00")) / qtd) if qtd else _reais(0)

    @staticmethod
    def _plural(qtd, palavra):
        return f"{qtd} {palavra}{'' if qtd == 1 else 's'}"

    # -------------------------------------------------------------- tipos
    def _total(self, pedidos, validos, totais):
        cancelados = pedidos.filter(status=Pedidos.StatusPedido.CANCELADO).count()
        soma = totais["soma_total"] or Decimal("0.00")
        taxa = totais["soma_taxa"] or Decimal("0.00")
        return {
            "titulo": "Total do período", "icone": "fa-sack-dollar", "tom": "total",
            "descricao": "Soma dos pedidos (sem a taxa do motoca), dividida por forma de pagamento.",
            "valor": _reais(soma),
            "destaques": [
                {"rotulo": "Pedidos", "valor": str(totais["qtd_total"])},
                {"rotulo": "Ticket médio", "valor": "R$ " + self._media(soma, totais["qtd_total"])},
                {"rotulo": "Taxa motoca", "valor": "R$ " + _reais(taxa)},
                {"rotulo": "Com as taxas", "valor": "R$ " + _reais(soma + taxa)},
            ],
            "quebra": self._quebra([
                ("Dinheiro", totais["soma_dinheiro"], "dinheiro", self._plural(totais["qtd_dinheiro"], "pedido")),
                ("Pix", totais["soma_pix"], "pix", self._plural(totais["qtd_pix"], "pedido")),
                ("Cartão", totais["soma_cartao"], "cartao", self._plural(totais["qtd_cartao"], "pedido")),
                ("Misto", totais["soma_misto"], "misto", self._plural(totais["qtd_misto"], "pedido")),
            ]),
            "nota": (
                f"{self._plural(cancelados, 'pedido')} cancelado{'' if cancelados == 1 else 's'} "
                "não entra" + ("" if cancelados == 1 else "m") + " na conta."
            ) if cancelados else "",
            **self._lista(validos),
        }

    def _pagamento(self, validos, totais, forma, nome, icone):
        selecao = validos.filter(forma_pagamento=forma)
        partes = selecao.aggregate(
            soma_retirada=Sum("total", filter=Q(entrega=False)),
            soma_entrega=Sum("total", filter=Q(entrega=True)),
            qtd_retirada=Count("id", filter=Q(entrega=False)),
            qtd_entrega=Count("id", filter=Q(entrega=True)),
            taxas=Sum("taxa_motoca"),
        )
        chave = self.TONS[forma]
        soma = totais[f"soma_{chave}"] or Decimal("0.00")
        qtd = totais[f"qtd_{chave}"]
        geral = totais["soma_total"] or Decimal("0.00")
        return {
            "titulo": nome, "icone": icone, "tom": chave,
            "descricao": f"Pedidos pagos em {nome.lower()}, sem a taxa do motoca.",
            "valor": _reais(soma),
            "destaques": [
                {"rotulo": "Pedidos", "valor": str(qtd)},
                {"rotulo": "Ticket médio", "valor": "R$ " + self._media(soma, qtd)},
                {"rotulo": "Do total", "valor": (f"{float(soma / geral * 100):.1f}%".replace(".", ",") if geral else "0%")},
                {"rotulo": "Taxas nesses pedidos", "valor": "R$ " + _reais(partes["taxas"])},
            ],
            "quebra": self._quebra([
                ("Retirada no balcão", partes["soma_retirada"], "retirada", self._plural(partes["qtd_retirada"], "pedido")),
                ("Entrega", partes["soma_entrega"], "entrega", self._plural(partes["qtd_entrega"], "pedido")),
            ]),
            **self._lista(selecao),
        }

    def _taxa(self, pedidos, validos, totais):
        entregas = validos.filter(entrega=True)
        partes = entregas.aggregate(
            com_taxa=Count("id", filter=Q(taxa_motoca__gt=0)),
            **{
                f"taxa_{chave}": Sum("taxa_motoca", filter=Q(forma_pagamento=forma))
                for forma, chave in self.TONS.items()
            },
            **{
                f"qtd_{chave}": Count("id", filter=Q(forma_pagamento=forma, taxa_motoca__gt=0))
                for forma, chave in self.TONS.items()
            },
        )
        soma = totais["soma_taxa"] or Decimal("0.00")
        qtd = totais["qtd_entregas"]
        return {
            "titulo": "Taxa do motoca", "icone": "fa-motorcycle", "tom": "taxa",
            "descricao": "Taxas de entrega do período e como cada entrega foi paga.",
            "valor": _reais(soma),
            "destaques": [
                {"rotulo": "Entregas", "valor": str(qtd)},
                {"rotulo": "Com taxa", "valor": str(partes["com_taxa"])},
                {"rotulo": "Taxa média", "valor": "R$ " + self._media(soma, partes["com_taxa"])},
                {"rotulo": "Sem taxa", "valor": str(qtd - partes["com_taxa"])},
            ],
            "quebra": self._quebra([
                (nome, partes[f"taxa_{chave}"], chave, self._plural(partes[f"qtd_{chave}"], "entrega"))
                for chave, (_forma, nome, _icone) in self.PAGAMENTOS.items()
            ]),
            **self._lista(
                entregas,
                valor=lambda p: p.taxa_motoca,
                detalhe=lambda p: ", ".join(x for x in [p.rua or "", p.numero or ""] if x) or "Sem endereço",
            ),
        }

    def _bebidas(self, pedidos, validos, totais):
        lista = _ranking_bebidas(ItensPedido.objects.filter(pedidos__in=validos))
        receita = sum((item["receita"] for item in lista), Decimal("0.00"))
        unidades = sum(item["unidades"] for item in lista)
        # Cada pedido aparece uma vez, com o valor só das bebidas dele.
        com_bebida = validos.annotate(
            valor_bebidas=Coalesce(
                Sum("itens__subtotal", filter=_q_bebidas("itens__produto__categoria__nome_categoria")),
                ZERO_REAIS, output_field=DINHEIRO_14,
            )
        ).filter(valor_bebidas__gt=0) if settings.PALAVRAS_BEBIDAS else validos.none()
        return {
            "titulo": "Bebidas", "icone": "fa-bottle-water", "tom": "bebidas",
            "descricao": "Itens de bebida (preço × quantidade), sem os pedidos cancelados.",
            "valor": _reais(receita),
            "destaques": [
                {"rotulo": "Unidades", "valor": str(unidades)},
                {"rotulo": "Pedidos com bebida", "valor": str(com_bebida.count())},
                {"rotulo": "Tipos de bebida", "valor": str(len(lista))},
            ],
            "itens": [
                {
                    "nome": item["produto__nome_produto"] or "Produto removido",
                    "unidades": item["unidades"],
                    "valor": _reais(item["receita"]),
                    "pct": round(float(item["receita"] / receita * 100), 1) if receita else 0,
                }
                for item in lista
            ],
            **self._lista(com_bebida, valor=lambda p: p.valor_bebidas),
        }

    def _retirada_dinheiro(self, pedidos, validos, totais):
        selecao = validos.filter(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO, entrega=False)
        qtd = totais["qtd_dinheiro_retirada"]
        return {
            "titulo": "Retirada em dinheiro", "icone": "fa-store", "tom": "retirada",
            "descricao": "Dinheiro recebido no balcão, nos pedidos de retirada.",
            "valor": _reais(totais["dinheiro_retirada"]),
            "destaques": [
                {"rotulo": "Pedidos", "valor": str(qtd)},
                {"rotulo": "Ticket médio", "valor": "R$ " + self._media(totais["dinheiro_retirada"], qtd)},
            ],
            **self._lista(selecao),
        }

    def _entrega_dinheiro(self, pedidos, validos, totais):
        selecao = validos.filter(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO, entrega=True)
        return {
            "titulo": "Entrega em dinheiro", "icone": "fa-motorcycle", "tom": "entrega",
            "descricao": "O que o motoca recebeu em dinheiro: o pedido mais a taxa de entrega.",
            "valor": _reais(totais["dinheiro_entrega"]),
            "destaques": [
                {"rotulo": "Entregas", "valor": str(totais["qtd_dinheiro_entrega"])},
                {"rotulo": "Pedidos", "valor": "R$ " + _reais(totais["soma_dinheiro_entrega_pedidos"])},
                {"rotulo": "Taxas", "valor": "R$ " + _reais(totais["soma_dinheiro_entrega_taxas"])},
            ],
            **self._lista(
                selecao,
                valor=lambda p: p.total + p.taxa_motoca,
                detalhe=lambda p: ", ".join(x for x in [p.rua or "", p.numero or ""] if x) or "Entrega",
            ),
        }

    def _caixa_dinheiro(self, pedidos, validos, totais):
        selecao = validos.filter(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO)
        return {
            "titulo": "Dinheiro no caixa", "icone": "fa-cash-register", "tom": "caixa",
            "descricao": "Valor físico esperado no caixa: retiradas + entregas pagas em dinheiro (com a taxa).",
            "valor": _reais(totais["dinheiro_caixa"]),
            "destaques": [
                {"rotulo": "Pedidos em dinheiro", "valor": str(totais["qtd_dinheiro"])},
                {"rotulo": "Retiradas", "valor": str(totais["qtd_dinheiro_retirada"])},
                {"rotulo": "Entregas", "valor": str(totais["qtd_dinheiro_entrega"])},
            ],
            "quebra": self._quebra([
                ("Retirada no balcão", totais["dinheiro_retirada"], "retirada",
                 self._plural(totais["qtd_dinheiro_retirada"], "pedido")),
                ("Pedidos entregues", totais["soma_dinheiro_entrega_pedidos"], "entrega",
                 self._plural(totais["qtd_dinheiro_entrega"], "pedido")),
                ("Taxas do motoca", totais["soma_dinheiro_entrega_taxas"], "taxa", ""),
            ]),
            **self._lista(selecao, valor=lambda p: p.total + p.taxa_motoca),
        }


class PedidoReimprimirView(LoginRequiredMixin, View):
    login_url = "login"

    def post(self, request, pedido_id):
        pedido = get_object_or_404(
            Pedidos.objects.prefetch_related("itens__produto"),
            id=pedido_id
        )

        itens_pedido = list(pedido.itens.all())

        # Nunca devolve uma reimpressão sem itens.
        if not itens_pedido:
            return JsonResponse({
                "success": False,
                "message": (
                    "Este pedido está sem itens relacionados no banco. "
                    "A reimpressão foi bloqueada para não imprimir uma via vazia."
                )
            }, status=409)

        itens_impressao = []

        for item in itens_pedido:
            adicionais = item.adicionais or []
            soma_adicionais = Decimal("0.00")
            adicionais_normalizados = []

            for adicional in adicionais:
                if not isinstance(adicional, dict):
                    continue

                try:
                    qtd_adicional = int(adicional.get("qtd", 1))
                    preco_adicional = Decimal(str(adicional.get("preco", 0)))
                except (InvalidOperation, TypeError, ValueError):
                    qtd_adicional = 1
                    preco_adicional = Decimal("0.00")

                if qtd_adicional <= 0:
                    continue

                soma_adicionais += preco_adicional * qtd_adicional
                adicionais_normalizados.append({
                    "nome": (adicional.get("nome") or "").strip(),
                    "qtd": qtd_adicional,
                    "preco": float(preco_adicional)
                })

            # No banco, preco_unitario já contém produto + adicionais.
            # Subtrai os adicionais para não somá-los duas vezes no JavaScript.
            preco_base = Decimal(str(item.preco_unitario)) - soma_adicionais
            if preco_base < 0:
                preco_base = Decimal("0.00")

            itens_impressao.append({
                "id": item.id,
                "produtoId": item.produto_id,
                "nome": item.produto.nome_produto if item.produto else "Item",
                "qtd": item.quantidade,
                "precoBase": float(preco_base),
                "precoUnitario": float(item.preco_unitario),
                "subtotal": float(item.subtotal),
                "adicionais": adicionais_normalizados
            })

        # A reimpressão só monta os dados. O navegador/QZ Tray é quem
        # efetivamente imprime; portanto não marcamos como impresso aqui.
        data = {
            "id": pedido.id,
            "criadoEm": timezone.localtime(pedido.criado_em).strftime(
                "%d/%m/%Y %H:%M"
            ),
            "nomeCliente": pedido.nome_cliente or "SEM NOME",
            "metodo": pedido.get_forma_pagamento_display(),
            "entrega": pedido.entrega,
            "descricao": pedido.descricao or "",
            "totalPedido": float(pedido.total),
            "taxaMotoca": float(pedido.taxa_motoca),
            "totalFinal": float(pedido.total + pedido.taxa_motoca),
            "endereco": {
                "rua": pedido.rua or "",
                "numero": pedido.numero or "",
                "cep": pedido.cep or "",
                "telefone": pedido.telefone or "",
            },
            "itens": itens_impressao
        }

        return JsonResponse(data)

def _reais(valor):
    """1234.5 → "1.234,50" (antes saía "53140,3000000000")."""
    valor = Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class VendasView(LoginRequiredMixin, View):
    template_name = "vendas.html"
    login_url = "login"
    # A página listava TODOS os pedidos já feitos, com os itens de cada um:
    # crescia todo dia e ficava cada vez mais lenta. Mostra os mais recentes;
    # os totais continuam sendo de tudo.
    LIMITE = 60

    def get(self, request, *args, **kwargs):
        totais = Pedidos.objects.aggregate(
            total_finalizados=Coalesce(
                Sum("total", filter=Q(status=Pedidos.StatusPedido.FINALIZADO)), ZERO_REAIS, output_field=DINHEIRO_14
            ),
            total_cancelados=Coalesce(
                Sum("total", filter=Q(status=Pedidos.StatusPedido.CANCELADO)), ZERO_REAIS, output_field=DINHEIRO_14
            ),
            qtd_finalizados=Count("id", filter=Q(status=Pedidos.StatusPedido.FINALIZADO)),
            qtd_cancelados=Count("id", filter=Q(status=Pedidos.StatusPedido.CANCELADO)),
        )

        def recentes(status):
            return (
                Pedidos.objects
                .filter(status=status)
                .prefetch_related("itens__produto")
                .order_by("-criado_em")[:self.LIMITE]
            )

        context = {
            "pedidos_finalizados": recentes(Pedidos.StatusPedido.FINALIZADO),
            "pedidos_cancelados": recentes(Pedidos.StatusPedido.CANCELADO),
            "total_finalizados": _reais(totais["total_finalizados"]),
            "total_cancelados": _reais(totais["total_cancelados"]),
            "qtd_finalizados": totais["qtd_finalizados"],
            "qtd_cancelados": totais["qtd_cancelados"],
            "limite": self.LIMITE,
        }
        return render(request, self.template_name, context)


@login_required
@require_POST
def avancar_status(request, pedido_id):
    """Leva o pedido para a próxima etapa (Pago → Preparo → Entrega → Finalizado).

    Antes: aceitava qualquer visitante sem login e, fora do AJAX, redirecionava
    para "historico_pedidos" — uma rota que não existe (erro 500).
    """
    pedido = get_object_or_404(Pedidos, id=pedido_id)
    ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    proximo = pedido.proximo_status()

    confirmacao = request.POST.get("confirmacao", "").strip().upper()
    if (
        proximo == Pedidos.StatusPedido.FINALIZADO
        and pedido.forma_pagamento == Pedidos.FormaPagamento.DINHEIRO
        and confirmacao != "CONFIRMAR"
    ):
        if ajax:
            return JsonResponse({"status": "erro", "mensagem": "Confirmação inválida."}, status=400)
        return redirect("pedidos")

    if proximo:
        Pedidos.objects.filter(pk=pedido.pk).update(status=proximo, atualizado=timezone.now())
        if ajax:
            return JsonResponse({"status": "sucesso", "mensagem": "Status avançado!", "novo_status": proximo})
    elif ajax:
        return JsonResponse({"status": "erro", "mensagem": "Este pedido não tem próxima etapa."}, status=400)

    return redirect("pedidos")


class GerenciarPratosDiaView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        # Só os dias em que a loja abre (segunda a sexta). Uma consulta só,
        # agrupada aqui — antes era uma consulta por dia da semana.
        dias_abertos = set(settings.LOJA_DIAS)
        produtos = Produtos.objects.filter(ativo=True).order_by('nome_produto')
        pratos_cadastrados = list(
            PratoDoDia.objects.filter(ativo=True).select_related('produto').order_by('produto__nome_produto')
        )

        agenda = []
        for num_dia, nome_dia in PratoDoDia.DIAS_SEMANA:
            if num_dia not in dias_abertos:
                continue
            agenda.append({
                'num_dia': num_dia,
                'nome_dia': nome_dia,
                'pratos': [p for p in pratos_cadastrados if p.dia_semana == num_dia],
            })

        return render(request, 'gerenciar_pratos_dia.html', {
            'agenda': agenda,
            'produtos': produtos
        })

    def post(self, request):
        # View para adicionar um novo prato a um dia específico
        dia_semana = request.POST.get('dia_semana')
        produto_id = request.POST.get('produto_id')

        if dia_semana and produto_id:
            try:
                produto = Produtos.objects.get(id=produto_id)
                # Verifica se o prato já não está cadastrado neste dia para evitar duplicação
                if not PratoDoDia.objects.filter(dia_semana=dia_semana, produto=produto, ativo=True).exists():
                    PratoDoDia.objects.create(dia_semana=dia_semana, produto=produto, ativo=True)
                    _limpar_cache_cardapio()
                    messages.success(request, 'Prato adicionado com sucesso!')
                else:
                    messages.warning(request, 'Este prato já está no cardápio deste dia.')
            except Produtos.DoesNotExist:
                messages.error(request, 'Produto não encontrado.')

        return redirect('gerenciar_pratos_dia')


class RemoverPratoDiaView(LoginRequiredMixin, View):
    login_url = "login"

    def post(self, request, pk):
        prato_dia = get_object_or_404(PratoDoDia, pk=pk)
        prato_dia.delete()
        _limpar_cache_cardapio()
        messages.success(request, 'Prato removido do dia com sucesso.')
        return redirect('gerenciar_pratos_dia')


class LoginView(View):
    template_name = "login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("pedidos")
        return render(request, self.template_name)

    def post(self, request):
        identificador = request.POST.get("identificador", "").strip()
        senha = request.POST.get("senha", "").strip()

        if not identificador or not senha:
            messages.error(request, "Preencha usuário/e-mail e senha.")
            return render(request, self.template_name)

        username_para_login = identificador

        if "@" in identificador:
            try:
                user_obj = User.objects.get(email__iexact=identificador)
                username_para_login = user_obj.username
            except User.DoesNotExist:
                messages.error(request, "Usuário não encontrado.")
                return render(request, self.template_name)

        user = authenticate(request, username=username_para_login, password=senha)

        if user is not None:
            login(request, user)
            return redirect("pedidos")

        messages.error(request, "Login ou senha inválidos.")
        return render(request, self.template_name)



class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect("login")





# ExtractWeekDay: 1 = domingo ... 7 = sábado (igual em SQLite e PostgreSQL).
DIAS_SEMANA_CURTO = {2: "Seg", 3: "Ter", 4: "Qua", 5: "Qui", 6: "Sex", 7: "Sáb", 1: "Dom"}


def _variacao(atual, anterior):
    """Variação percentual contra o período anterior (None = sem base)."""
    atual = float(atual or 0)
    anterior = float(anterior or 0)
    if anterior <= 0:
        return None
    return round((atual - anterior) / anterior * 100, 1)


class DashboardAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = "dashboards.html"
    login_url = "login"

    STATUS_LABELS = dict(Pedidos.StatusPedido.choices)
    PAGAMENTO_LABELS = dict(Pedidos.FormaPagamento.choices)
    ATALHOS = [
        ("hoje", "Hoje"),
        ("7d", "7 dias"),
        ("30d", "30 dias"),
        ("mes", "Este mês"),
        ("mes_passado", "Mês passado"),
        ("tudo", "Tudo"),
    ]

    def _format_money(self, valor):
        valor = valor or Decimal("0.00")
        return (
            f"{valor:,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    def _parse_data(self, valor):
        try:
            return datetime.strptime(valor, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    def _periodo_do_atalho(self, atalho, primeira, ultima):
        hoje = timezone.localdate()
        if atalho == "hoje":
            return hoje, hoje
        if atalho == "7d":
            return hoje - timedelta(days=6), hoje
        if atalho == "mes":
            return hoje.replace(day=1), hoje
        if atalho == "mes_passado":
            fim = hoje.replace(day=1) - timedelta(days=1)
            return fim.replace(day=1), fim
        if atalho == "tudo":
            return primeira, ultima
        return hoje - timedelta(days=29), hoje   # "30d", o padrão

    def _get_datas(self):
        # Primeira e última data com pedido: um MIN/MAX, e não a lista de
        # todas as datas (que crescia com o histórico).
        limites = Pedidos.objects.aggregate(primeira=Min("criado_em"), ultima=Max("criado_em"))
        hoje = timezone.localdate()
        if not limites["primeira"]:
            return {
                "data_inicio": hoje,
                "data_fim": hoje,
                "primeira_data_registro": None,
                "ultima_data_registro": None,
                "datas_com_registro": [],
                "atalho": "",
            }

        primeira = timezone.localtime(limites["primeira"]).date()
        ultima = max(timezone.localtime(limites["ultima"]).date(), hoje)

        inicio = self._parse_data(self.request.GET.get("data_inicio"))
        fim = self._parse_data(self.request.GET.get("data_fim"))
        atalho = self.request.GET.get("atalho", "")
        if not (inicio and fim):
            atalho = atalho or "30d"
            inicio, fim = self._periodo_do_atalho(atalho, primeira, ultima)
        else:
            atalho = ""

        if inicio > fim:
            inicio, fim = fim, inicio

        return {
            "data_inicio": inicio,
            "data_fim": fim,
            "primeira_data_registro": primeira,
            "ultima_data_registro": ultima,
            "datas_com_registro": [primeira.strftime("%Y-%m-%d"), ultima.strftime("%Y-%m-%d")],
            "atalho": atalho,
        }

    def _serie_datas_completa(self, data_inicio, data_fim, vendas_por_data,
                              pagamentos_por_data=(), bebidas_por_data=()):
        mapa = {
            item["data"]: {
                "pedidos": item["pedidos"],
                "receita": float(item["receita"] or 0),
                "taxa": float(item.get("taxa") or 0),
            }
            for item in vendas_por_data
        }
        # Recebido por forma de pagamento em cada dia (o relatório da tela de
        # Pedidos, dia a dia) e as bebidas vendidas em cada dia.
        formas = [valor for valor, _ in Pedidos.FormaPagamento.choices]
        mapa_pagamentos = {}
        for item in pagamentos_por_data:
            mapa_pagamentos.setdefault(item["data"], {})[item["forma_pagamento"]] = float(item["receita"] or 0)
        mapa_bebidas = {
            item["data"]: (float(item["receita"] or 0), item["unidades"] or 0)
            for item in bebidas_por_data
        }

        labels, pedidos, receita, ticket, acumulado, taxas = [], [], [], [], [], []
        por_pagamento = {forma: [] for forma in formas}
        bebidas, bebidas_unidades = [], []
        soma = 0.0
        dia = data_inicio
        while dia <= data_fim:
            valores = mapa.get(dia, {"pedidos": 0, "receita": 0, "taxa": 0})
            # Fim de semana sem venda não entra (a loja não abre): a linha
            # não despenca a zero todo sábado e domingo.
            if dia.weekday() > 4 and not valores["pedidos"]:
                dia += timedelta(days=1)
                continue
            labels.append(dia.strftime("%d/%m"))
            pedidos.append(valores["pedidos"])
            receita.append(round(valores["receita"], 2))
            ticket.append(round(valores["receita"] / valores["pedidos"], 2) if valores["pedidos"] else 0)
            soma += valores["receita"]
            acumulado.append(round(soma, 2))
            taxas.append(round(valores["taxa"], 2))
            do_dia = mapa_pagamentos.get(dia, {})
            for forma in formas:
                por_pagamento[forma].append(round(do_dia.get(forma, 0), 2))
            receita_bebidas, unidades_bebidas = mapa_bebidas.get(dia, (0, 0))
            bebidas.append(round(receita_bebidas, 2))
            bebidas_unidades.append(unidades_bebidas)
            dia += timedelta(days=1)

        return {
            "labels": labels,
            "pedidos": pedidos,
            "receita": receita,
            "ticket": ticket,
            "acumulado": acumulado,
            "taxas": taxas,
            "por_pagamento": por_pagamento,
            "bebidas": bebidas,
            "bebidas_unidades": bebidas_unidades,
        }

    def _resumo(self, pedidos_validos):
        return pedidos_validos.aggregate(
            receita=Coalesce(Sum("total"), ZERO_REAIS, output_field=DINHEIRO_14),
            taxas=Coalesce(Sum("taxa_motoca"), ZERO_REAIS, output_field=DINHEIRO_14),
            pedidos=Count("id"),
            entregas=Count("id", filter=Q(entrega=True)),
        )

    def _bebidas(self, itens_validos, pedidos_validos, pedidos_anteriores, resumo, categorias_qs):
        """Relatório de bebidas do período: quanto vendem, quanto pesam no
        faturamento e se o pedido com bebida deixa mais dinheiro no caixa."""
        itens_bebidas = itens_validos.filter(_q_bebidas())
        ranking = _ranking_bebidas(itens_validos)
        receita = sum((item["receita"] for item in ranking), Decimal("0.00"))
        unidades = sum(item["unidades"] for item in ranking)

        receita_anterior = (
            ItensPedido.objects
            .filter(pedidos__in=pedidos_anteriores)
            .filter(_q_bebidas())
            .aggregate(receita=Coalesce(Sum("subtotal"), ZERO_REAIS, output_field=DINHEIRO_14))["receita"]
        )

        # Participação nas vendas de itens (comida + bebida, antes do desconto).
        receita_itens = sum((item["receita"] for item in categorias_qs), Decimal("0.00"))

        # Pedidos com pelo menos uma bebida: o subselect evita contar duas
        # vezes o pedido que levou duas bebidas.
        com_bebida = self._resumo(
            pedidos_validos.filter(pk__in=ItensPedido.objects.filter(_q_bebidas()).values("pedidos"))
        )
        sem_pedidos = resumo["pedidos"] - com_bebida["pedidos"]
        sem_receita = resumo["receita"] - com_bebida["receita"]
        ticket_com = com_bebida["receita"] / com_bebida["pedidos"] if com_bebida["pedidos"] else Decimal("0.00")
        ticket_sem = sem_receita / sem_pedidos if sem_pedidos else Decimal("0.00")
        maior_ticket = max(ticket_com, ticket_sem) or Decimal("1")

        por_dia = list(
            itens_bebidas
            .annotate(data=TruncDate("pedidos__criado_em"))
            .values("data")
            .annotate(
                unidades=Coalesce(Sum("quantidade"), Value(0), output_field=IntegerField()),
                receita=Coalesce(Sum("subtotal"), ZERO_REAIS, output_field=DINHEIRO_14),
            )
            .order_by("data")
        )

        top = ranking[:10]
        return {
            "receita": self._format_money(receita),
            "unidades": unidades,
            "variacao_receita": _variacao(receita, receita_anterior),
            "participacao": round(float(receita) / float(receita_itens) * 100, 1) if receita_itens else 0,
            "lider": (ranking[0]["produto__nome_produto"] or "Produto removido") if ranking else "Sem vendas",
            "lider_unidades": ranking[0]["unidades"] if ranking else 0,
            "pedidos_com": com_bebida["pedidos"],
            "pedidos_pct": round(com_bebida["pedidos"] / resumo["pedidos"] * 100, 1) if resumo["pedidos"] else 0,
            "ticket_com": self._format_money(ticket_com),
            "ticket_sem": self._format_money(ticket_sem),
            "ticket_com_pct": round(float(ticket_com / maior_ticket) * 100),
            "ticket_sem_pct": round(float(ticket_sem / maior_ticket) * 100),
            "ticket_diferenca": _variacao(ticket_com, ticket_sem) if com_bebida["pedidos"] else None,
            "ticket_diferenca_abs": abs(_variacao(ticket_com, ticket_sem) or 0),
            "ranking": [
                {
                    "nome": item["produto__nome_produto"] or "Produto removido",
                    "unidades": item["unidades"],
                    "receita": self._format_money(item["receita"]),
                    "pct": round(float(item["receita"]) / float(receita) * 100, 1) if receita else 0,
                }
                for item in ranking
            ],
            "por_dia": por_dia,
            "grafico_top": {
                "titulo": "Unidades",
                "labels": [item["produto__nome_produto"] or "Produto removido" for item in reversed(top)],
                "valores": [item["unidades"] for item in reversed(top)],
            },
        }

    def _get_dashboard_data(self):
        datas = self._get_datas()
        data_inicio = datas["data_inicio"]
        data_fim = datas["data_fim"]

        if datas["primeira_data_registro"]:
            pedidos_periodo = Pedidos.objects.filter(
                criado_em__date__range=(data_inicio, data_fim)
            )
        else:
            pedidos_periodo = Pedidos.objects.none()

        pedidos_validos = pedidos_periodo.exclude(status=Pedidos.StatusPedido.CANCELADO)

        # Tudo o que é soma/contagem dos pedidos sai de UMA consulta.
        resumo = self._resumo(pedidos_validos)
        receita_total = resumo["receita"]
        taxas_entrega = resumo["taxas"]
        total_pedidos = resumo["pedidos"]
        entregas = resumo["entregas"]
        retiradas = total_pedidos - entregas
        pedidos_cancelados = pedidos_periodo.filter(status=Pedidos.StatusPedido.CANCELADO).count()
        ticket_medio = receita_total / total_pedidos if total_pedidos else Decimal("0.00")

        # O PERÍODO ANTERIOR, do mesmo tamanho: é o que diz se o negócio
        # está crescendo ou caindo.
        dias = (data_fim - data_inicio).days + 1
        anterior_fim = data_inicio - timedelta(days=1)
        anterior_inicio = anterior_fim - timedelta(days=dias - 1)
        pedidos_anteriores = (
            Pedidos.objects
            .filter(criado_em__date__range=(anterior_inicio, anterior_fim))
            .exclude(status=Pedidos.StatusPedido.CANCELADO)
        )
        resumo_anterior = self._resumo(pedidos_anteriores)
        ticket_anterior = (
            resumo_anterior["receita"] / resumo_anterior["pedidos"]
            if resumo_anterior["pedidos"] else Decimal("0.00")
        )

        itens_validos = ItensPedido.objects.filter(pedidos__in=pedidos_validos)

        itens_resumo = itens_validos.aggregate(
            unidades=Coalesce(Sum("quantidade"), Value(0), output_field=IntegerField()),
            produtos=Count("produto_id", distinct=True),
        )

        ranking_produtos = list(
            itens_validos
            .values("produto__nome_produto")
            .annotate(
                total_quantidade=Coalesce(Sum("quantidade"), Value(0), output_field=IntegerField()),
                total_receita=Coalesce(Sum("subtotal"), ZERO_REAIS, output_field=DINHEIRO_14),
            )
            .order_by("-total_quantidade", "-total_receita", "produto__nome_produto")
        )
        for item in ranking_produtos:
            item["total_receita_formatada"] = self._format_money(item["total_receita"])

        produto_lider = ranking_produtos[0]["produto__nome_produto"] if ranking_produtos else "Sem vendas"
        produto_lider_quantidade = ranking_produtos[0]["total_quantidade"] if ranking_produtos else 0

        categorias_qs = list(
            itens_validos
            .values("produto__categoria__nome_categoria")
            .annotate(receita=Coalesce(Sum("subtotal"), ZERO_REAIS, output_field=DINHEIRO_14))
            .order_by("-receita")
        )

        vendas_diarias_qs = list(
            pedidos_validos
            .annotate(data=TruncDate("criado_em"))
            .values("data")
            .annotate(
                pedidos=Count("id"),
                receita=Coalesce(Sum("total"), ZERO_REAIS, output_field=DINHEIRO_14),
                taxa=Coalesce(Sum("taxa_motoca"), ZERO_REAIS, output_field=DINHEIRO_14),
            )
            .order_by("data")
        )

        pagamentos_diarios_qs = list(
            pedidos_validos
            .annotate(data=TruncDate("criado_em"))
            .values("data", "forma_pagamento")
            .annotate(receita=Coalesce(Sum("total"), ZERO_REAIS, output_field=DINHEIRO_14))
            .order_by("data")
        )

        bebidas = self._bebidas(
            itens_validos, pedidos_validos, pedidos_anteriores, resumo, categorias_qs
        )
        bebidas_por_dia = bebidas.pop("por_dia")
        grafico_top_bebidas = bebidas.pop("grafico_top")

        status_qs = list(
            pedidos_periodo.values("status").annotate(total=Count("id")).order_by("status")
        )

        pagamentos_qs = list(
            pedidos_validos
            .values("forma_pagamento")
            .annotate(
                qtd_pedidos=Count("id"),
                receita=Coalesce(Sum("total"), ZERO_REAIS, output_field=DINHEIRO_14),
            )
            .order_by("-receita")
        )

        horarios_qs = list(
            pedidos_validos
            .annotate(hora=ExtractHour("criado_em"))
            .values("hora")
            .annotate(total=Count("id"))
            .order_by("hora")
        )
        horarios_map = {int(item["hora"]): item["total"] for item in horarios_qs}
        if horarios_qs:
            pico = max(horarios_qs, key=lambda item: item["total"])
            horario_pico = f'{int(pico["hora"]):02d}:00'
            horario_pico_pedidos = pico["total"]
        else:
            horario_pico = "--:--"
            horario_pico_pedidos = 0
        # A janela do gráfico é o expediente (com uma hora de folga de cada
        # lado), e não as 24 horas do dia com 18 colunas vazias.
        hora_ini = min([settings.LOJA_ABRE - 1] + list(horarios_map))
        hora_fim = max([settings.LOJA_FECHA] + list(horarios_map))

        # Média de receita por dia da semana: qual dia vende mais.
        semana_qs = list(
            pedidos_validos
            .annotate(dia=ExtractWeekDay("criado_em"), data=TruncDate("criado_em"))
            .values("dia", "data")
            .annotate(receita=Coalesce(Sum("total"), ZERO_REAIS, output_field=DINHEIRO_14))
        )
        por_dia = {}
        for item in semana_qs:
            por_dia.setdefault(item["dia"], []).append(float(item["receita"]))
        dias_semana = [d for d in (2, 3, 4, 5, 6, 7, 1) if d in por_dia or d in (2, 3, 4, 5, 6)]
        media_dia_semana = [
            round(sum(por_dia.get(d, [])) / len(por_dia[d]), 2) if por_dia.get(d) else 0
            for d in dias_semana
        ]
        melhor_dia = (
            DIAS_SEMANA_CURTO[dias_semana[media_dia_semana.index(max(media_dia_semana))]]
            if any(media_dia_semana) else "--"
        )

        clicks_qs = ProdutosMaisClick.objects.select_related("produto")
        if datas["primeira_data_registro"]:
            clicks_qs = clicks_qs.filter(criacao__date__range=(data_inicio, data_fim))
        clicks_agrupados = list(
            clicks_qs
            .values("produto__nome_produto")
            .annotate(quantidade=Sum("quantidade"))
            .order_by("-quantidade", "produto__nome_produto")
        )
        produtos_click_todos = [
            {
                "produto": {"nome_produto": item["produto__nome_produto"] or "Produto removido"},
                "quantidade": item["quantidade"],
            }
            for item in clicks_agrupados
        ]

        top_quantidade = ranking_produtos[:10]
        top_receita = sorted(ranking_produtos, key=lambda item: item["total_receita"], reverse=True)[:10]
        top_clicks = produtos_click_todos[:10]
        taxa_cancelamento = (
            round(pedidos_cancelados / (total_pedidos + pedidos_cancelados) * 100, 1)
            if (total_pedidos + pedidos_cancelados) else 0
        )

        return {
            **datas,
            "atalhos": self.ATALHOS,
            "dias_periodo": dias,
            "periodo_anterior_inicio": anterior_inicio,
            "periodo_anterior_fim": anterior_fim,

            "receita_total": self._format_money(receita_total),
            "taxas_entrega": self._format_money(taxas_entrega),
            "total_pedidos": total_pedidos,
            "pedidos_cancelados": pedidos_cancelados,
            "taxa_cancelamento": taxa_cancelamento,
            "ticket_medio": self._format_money(ticket_medio),
            "unidades_vendidas": itens_resumo["unidades"],
            "produtos_distintos": itens_resumo["produtos"],
            "total_entregas": entregas,
            "total_retiradas": retiradas,
            "produto_lider": produto_lider,
            "produto_lider_quantidade": produto_lider_quantidade,
            "horario_pico": horario_pico,
            "horario_pico_pedidos": horario_pico_pedidos,
            "melhor_dia": melhor_dia,
            "variacao_receita": _variacao(receita_total, resumo_anterior["receita"]),
            "variacao_pedidos": _variacao(total_pedidos, resumo_anterior["pedidos"]),
            "variacao_ticket": _variacao(ticket_medio, ticket_anterior),

            "mais_vendidos": ranking_produtos[:5],
            "mais_vendidos_todos": ranking_produtos,
            "produtos_click": produtos_click_todos[:5],
            "produtos_click_todos": produtos_click_todos,

            "grafico_vendas_diarias": self._serie_datas_completa(
                data_inicio, data_fim, vendas_diarias_qs, pagamentos_diarios_qs, bebidas_por_dia
            ),
            "bebidas": bebidas,
            "grafico_top_bebidas": grafico_top_bebidas,
            "grafico_status": {
                "labels": [self.STATUS_LABELS.get(item["status"], item["status"]) for item in status_qs],
                "valores": [item["total"] for item in status_qs],
            },
            "grafico_pagamentos": {
                "labels": [
                    self.PAGAMENTO_LABELS.get(item["forma_pagamento"], item["forma_pagamento"])
                    for item in pagamentos_qs
                ],
                "valores": [float(item["receita"]) for item in pagamentos_qs],
                "pedidos": [item["qtd_pedidos"] for item in pagamentos_qs],
            },
            "grafico_entrega": {
                "labels": ["Entrega", "Retirada"],
                "valores": [entregas, retiradas],
            },
            "grafico_horarios": {
                "labels": [f"{hora:02d}h" for hora in range(hora_ini, hora_fim + 1)],
                "valores": [horarios_map.get(hora, 0) for hora in range(hora_ini, hora_fim + 1)],
            },
            "grafico_dia_semana": {
                "labels": [DIAS_SEMANA_CURTO[d] for d in dias_semana],
                "valores": media_dia_semana,
            },
            "grafico_categorias": {
                "labels": [item["produto__categoria__nome_categoria"] or "Sem categoria" for item in categorias_qs],
                "valores": [float(item["receita"]) for item in categorias_qs],
            },
            "grafico_top_produtos": {
                "titulo": "Unidades",
                "labels": [item["produto__nome_produto"] or "Produto removido" for item in reversed(top_quantidade)],
                "valores": [item["total_quantidade"] for item in reversed(top_quantidade)],
            },
            "grafico_top_receita": {
                "titulo": "Receita",
                "labels": [item["produto__nome_produto"] or "Produto removido" for item in reversed(top_receita)],
                "valores": [float(item["total_receita"]) for item in reversed(top_receita)],
            },
            "grafico_cliques": {
                "titulo": "Cliques",
                "labels": [item["produto"]["nome_produto"] for item in reversed(top_clicks)],
                "valores": [item["quantidade"] for item in reversed(top_clicks)],
            },
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_dashboard_data())
        return context

    def get(self, request, *args, **kwargs):
        if request.GET.get("ajax") == "1":
            dados = self._get_dashboard_data()
            return JsonResponse(dados, safe=False)

        return super().get(request, *args, **kwargs)
