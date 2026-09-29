import hmac
import json
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

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


def _produto_para_cardapio(produto):
    return {
        "id": produto.id,
        "nome": produto.nome_produto,
        "preco": f"{produto.preco:.2f}",
        "codigo": produto.codigo,
        "imagem": _url_imagem(produto.image_produto),
        "tem_adicionais": any(a.ativo for a in produto.adicionais_disponiveis.all()),
    }


# O catálogo muda pouco durante o dia: fica 60 s na memória da função. Na
# Vercel isso poupa o banco a cada visita de cliente (a função quente
# reaproveita a memória entre requisições).
CARDAPIO_CACHE_SEGUNDOS = 60


def _dados_do_cardapio():
    dia_hoje = timezone.localdate().weekday()
    chave = f"cardapio:v2:{dia_hoje}"
    dados = cache.get(chave)
    if dados is not None:
        return dados

    produtos_ativos = (
        Produtos.objects
        .filter(ativo=True)
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
        .select_related("produto")
        .prefetch_related("produto__adicionais_disponiveis")
    )
    dados = {
        "categorias": [
            {
                "id": categoria.id,
                "nome": categoria.nome_categoria,
                "produtos": [_produto_para_cardapio(p) for p in categoria.produtos.all()],
            }
            for categoria in categorias
        ],
        "pratos_do_dia": [_produto_para_cardapio(prato.produto) for prato in pratos],
    }
    dados["categorias"] = [c for c in dados["categorias"] if c["produtos"]]
    cache.set(chave, dados, CARDAPIO_CACHE_SEGUNDOS)
    return dados


def _limpar_cache_cardapio():
    cache.delete_many([f"cardapio:v2:{d}" for d in range(7)])


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


def adicionais_produto(request, produto_id):
    produto = get_object_or_404(Produtos, id=produto_id)
    adicionais = produto.adicionais_disponiveis.filter(ativo=True)

    data = [
        {
            "nome": adicional.nome,
            "preco": str(adicional.preco)
        }
        for adicional in adicionais
    ]

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
        .prefetch_related("itens", "itens__produto")
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
            "total_dia": _formatar_moeda(totais["soma_total"]),
            "total_dinheiro": _formatar_moeda(totais["soma_dinheiro"]),
            "total_pix": _formatar_moeda(totais["soma_pix"]),
            "total_cartao": _formatar_moeda(totais["soma_cartao"]),
            "total_misto": _formatar_moeda(totais["soma_misto"]),
            "total_taxa_motoca": _formatar_moeda(totais["soma_taxa"]),

            # Novos valores para conferência do caixa físico.
            "dinheiro_retirada": _formatar_moeda(totais["dinheiro_retirada"]),
            "dinheiro_entrega": _formatar_moeda(totais["dinheiro_entrega"]),
            "dinheiro_caixa": _formatar_moeda(totais["dinheiro_caixa"]),

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

        return JsonResponse({
            "label": label,
            "total_dia": _formatar_moeda(totais["soma_total"]),
            "total_taxa": _formatar_moeda(totais["soma_taxa"]),
            "total_dinheiro": _formatar_moeda(totais["soma_dinheiro"]),
            "total_pix": _formatar_moeda(totais["soma_pix"]),
            "total_cartao": _formatar_moeda(totais["soma_cartao"]),
            "total_misto": _formatar_moeda(totais["soma_misto"]),

            # Novos valores, também obedecendo aos filtros atuais.
            "dinheiro_retirada": _formatar_moeda(totais["dinheiro_retirada"]),
            "dinheiro_entrega": _formatar_moeda(totais["dinheiro_entrega"]),
            "dinheiro_caixa": _formatar_moeda(totais["dinheiro_caixa"]),
        })


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

    def _serie_datas_completa(self, data_inicio, data_fim, vendas_por_data):
        mapa = {
            item["data"]: {
                "pedidos": item["pedidos"],
                "receita": float(item["receita"] or 0),
            }
            for item in vendas_por_data
        }

        labels, pedidos, receita, ticket, acumulado = [], [], [], [], []
        soma = 0.0
        dia = data_inicio
        while dia <= data_fim:
            valores = mapa.get(dia, {"pedidos": 0, "receita": 0})
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
            dia += timedelta(days=1)

        return {
            "labels": labels,
            "pedidos": pedidos,
            "receita": receita,
            "ticket": ticket,
            "acumulado": acumulado,
        }

    def _resumo(self, pedidos_validos):
        return pedidos_validos.aggregate(
            receita=Coalesce(Sum("total"), ZERO_REAIS, output_field=DINHEIRO_14),
            taxas=Coalesce(Sum("taxa_motoca"), ZERO_REAIS, output_field=DINHEIRO_14),
            pedidos=Count("id"),
            entregas=Count("id", filter=Q(entrega=True)),
        )

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
        resumo_anterior = self._resumo(
            Pedidos.objects
            .filter(criado_em__date__range=(anterior_inicio, anterior_fim))
            .exclude(status=Pedidos.StatusPedido.CANCELADO)
        )
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
            )
            .order_by("data")
        )

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

            "grafico_vendas_diarias": self._serie_datas_completa(data_inicio, data_fim, vendas_diarias_qs),
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
