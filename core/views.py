from django.db.models import Sum, Count, Prefetch
from django.shortcuts import render, get_object_or_404, redirect
from .models import Produtos, EstoqueProdutos, CategoriaProdutos, Pedidos, ItensPedido, PratoDoDia
from django.views.generic import View
import json
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages


from decimal import Decimal
from datetime import datetime, time, timedelta

from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin

class CardapioClienteView(View):
    def get(self, request):
        produtos_ativos = Produtos.objects.filter(ativo=True).prefetch_related("adicionais_disponiveis")

        categorias = CategoriaProdutos.objects.filter(
            ativo=True,
            produtos__ativo=True
        ).annotate(
            total_produtos=Count('produtos')
        ).filter(
            total_produtos__gt=0
        ).prefetch_related(
            Prefetch("produtos", queryset=produtos_ativos)
        ).distinct()

        dia_hoje = timezone.localdate().weekday()
        pratos_do_dia = PratoDoDia.objects.filter(
            dia_semana=dia_hoje,
            ativo=True,
            produto__ativo=True
        ).select_related("produto")

        agora = timezone.localtime()
        loja_aberta = 0 <= agora.weekday() <= 5 and 11 <= agora.hour < 16

        return render(request, "index.html", {
            "categorias": categorias,
            "pratos_do_dia": pratos_do_dia,
            "loja_aberta_server": loja_aberta
        })


from decimal import Decimal, InvalidOperation

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
        try:
            data = json.loads(request.body)

            carrinho = data.get("carrinho", [])
            metodo_pagamento = data.get("metodo_pagamento")
            tipo_entrega = data.get("tipo_entrega")
            nome_cliente = (data.get("nome_cliente") or "").strip()
            descricao = (data.get("descricao") or "").strip()

            endereco = data.get("endereco", {}) or {}
            cep = (endereco.get("cep") or "").strip()
            rua = (endereco.get("rua") or "").strip()
            numero = (endereco.get("numero") or "").strip()

            try:
                total = Decimal(str(data.get("total", 0)))
                taxa_motoca = Decimal(str(data.get("taxa_motoca", 0)))
            except (InvalidOperation, TypeError, ValueError):
                return JsonResponse(
                    {"success": False, "message": "Valores monetários inválidos."},
                    status=400
                )

            if not carrinho:
                return JsonResponse(
                    {"success": False, "message": "Carrinho vazio"},
                    status=400
                )

            if not nome_cliente:
                return JsonResponse(
                    {"success": False, "message": "Nome do cliente é obrigatório"},
                    status=400
                )

            if not metodo_pagamento:
                return JsonResponse(
                    {"success": False, "message": "Método de pagamento é obrigatório"},
                    status=400
                )

            if tipo_entrega == "entrega" and (not rua or not numero):
                return JsonResponse(
                    {"success": False, "message": "Rua e número são obrigatórios para entrega."},
                    status=400
                )

            with transaction.atomic():
                pedido = Pedidos.objects.create(
                    nome_cliente=nome_cliente,
                    descricao=descricao or None,
                    total=total,
                    taxa_motoca=taxa_motoca,
                    forma_pagamento=metodo_pagamento.upper(),
                    entrega=(tipo_entrega == "entrega"),
                    cep=cep or None,
                    rua=rua or None,
                    numero=numero or None,
                    impresso=False
                )

                itens = []

                for item in carrinho:
                    produto = Produtos.objects.get(id=item["id"])

                    qtd = int(item["qtd"])
                    adicionais = item.get("adicionais", []) or []

                    preco_base = Decimal(str(item["precoBase"]))
                    soma_adicionais = sum(
                        Decimal(str(a["preco"])) * Decimal(str(a.get("qtd", 1)))
                        for a in adicionais
                    )

                    preco_unitario = preco_base + soma_adicionais
                    subtotal_item = preco_unitario * qtd

                    item_pedido = ItensPedido.objects.create(
                        produto=produto,
                        quantidade=qtd,
                        preco_unitario=preco_unitario,
                        subtotal=subtotal_item,
                        adicionais=adicionais
                    )

                    itens.append(item_pedido)

                pedido.itens.set(itens)

            return JsonResponse({
                "success": True,
                "pedido_id": pedido.id
            })

        except Produtos.DoesNotExist:
            return JsonResponse({
                "success": False,
                "message": "Produto não encontrado"
            }, status=404)

        except Exception as e:
            return JsonResponse({
                "success": False,
                "message": str(e)
            }, status=500)


# Novo endpoint de confirmação
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@csrf_exempt
@require_POST
def confirmar_impressao(request, pedido_id):
    chave = request.headers.get('X-API-Key')
    if chave != 'chave-secreta-restaurante-2026':
        return JsonResponse({'erro': 'Não autorizado'}, status=401)

    atualizado = Pedidos.objects.filter(id=pedido_id, impresso=False).update(impresso=True)
    if atualizado:
        return JsonResponse({'ok': True})
    return JsonResponse({'erro': 'Pedido não encontrado ou já impresso'}, status=404)


def pedidos_pendentes_impressao(request):
    """Retorna pedidos com impresso=False — NÃO marca como impresso aqui"""
    if request.headers.get('X-API-Key') != 'chave-secreta-restaurante-2026':
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
            'total': str(pedido.total),
            'taxa_motoca': str(pedido.taxa_motoca),
            'criado_em': pedido.criado_em.strftime('%d/%m/%Y %H:%M'),
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



from datetime import datetime, time, timedelta
from decimal import Decimal
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from .models import Pedidos


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


from datetime import timedelta
from django.db.models import Q, Sum
from django.utils import timezone


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



class PedidosView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        pedidos_filtrados, periodo, pagamentos, label = _filtrar_pedidos(request)

        pedidos_totais = pedidos_filtrados.exclude(
            status=Pedidos.StatusPedido.CANCELADO
        )

        totais = pedidos_totais.aggregate(
            soma_total=Sum("total"),
            soma_taxa=Sum("taxa_motoca"),

            soma_dinheiro=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO)
            ),
            soma_pix=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.PIX)
            ),
            soma_cartao=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.CARTAO)
            ),
            soma_misto=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.MISTO)
            ),
        )

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

        totais = pedidos_totais.aggregate(
            soma_total=Sum("total"),
            soma_taxa=Sum("taxa_motoca"),

            soma_dinheiro=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO)
            ),
            soma_pix=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.PIX)
            ),
            soma_cartao=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.CARTAO)
            ),
            soma_misto=Sum(
                "total",
                filter=Q(forma_pagamento=Pedidos.FormaPagamento.MISTO)
            ),
        )

        return JsonResponse({
            "label": label,
            "total_dia": _formatar_moeda(totais["soma_total"]),
            "total_taxa": _formatar_moeda(totais["soma_taxa"]),
            "total_dinheiro": _formatar_moeda(totais["soma_dinheiro"]),
            "total_pix": _formatar_moeda(totais["soma_pix"]),
            "total_cartao": _formatar_moeda(totais["soma_cartao"]),
            "total_misto": _formatar_moeda(totais["soma_misto"]),
        })

class PedidoReimprimirView(LoginRequiredMixin, View):
    def post(self, request, pedido_id):
        pedido = get_object_or_404(Pedidos, id=pedido_id)

        # marca como impresso
        pedido.impresso = True
        pedido.save()

        data = {
            "id": pedido.id,
            "criadoEm": pedido.criado_em.strftime("%d/%m/%Y %H:%M"),
            "nomeCliente": pedido.nome_cliente or "SEM NOME",
            "metodo": pedido.get_forma_pagamento_display(),
            "entrega": pedido.entrega,
            "descricao": pedido.descricao or "",
            "totalPedido": float(pedido.total),
            "taxaMotoca": float(pedido.taxa_motoca),
            "totalFinal": float(pedido.total + pedido.taxa_motoca),
            "endereco": {
                "rua": pedido.rua,
                "numero": pedido.numero,
                "cep": pedido.cep,
            },
            "itens": [
                {
                    "nome": item.produto.nome_produto if item.produto else "Item",
                    "qtd": item.quantidade,
                    "precoBase": float(item.preco_unitario),
                    "adicionais": item.adicionais or []
                }
                for item in pedido.itens.all()
            ]
        }

        return JsonResponse(data)

class VendasView(LoginRequiredMixin, View):
    template_name = "vendas.html"
    login_url = "login"  # ajuste se sua url de login tiver outro nome

    def get(self, request, *args, **kwargs):
        pedidos_finalizados = Pedidos.objects.filter(
            status=Pedidos.StatusPedido.FINALIZADO
        ).prefetch_related('itens__produto')

        pedidos_cancelados = Pedidos.objects.filter(
            status=Pedidos.StatusPedido.CANCELADO
        ).prefetch_related('itens__produto')

        total_finalizados = pedidos_finalizados.aggregate(
            total=Sum('total')
        )['total'] or 0

        total_cancelados = pedidos_cancelados.aggregate(
            total=Sum('total')
        )['total'] or 0

        context = {

            "pedidos_finalizados": pedidos_finalizados,
            "pedidos_cancelados": pedidos_cancelados,
            "total_finalizados": total_finalizados,
            "total_cancelados": total_cancelados,

        }

        return render(request, self.template_name, context)


def avancar_status(request, pedido_id):
    pedido = get_object_or_404(Pedidos, id=pedido_id)

    if request.method == "POST":
        proximo = pedido.proximo_status()

        # Verificação extra de segurança no backend
        confirmacao = request.POST.get('confirmacao', '').strip().upper()
        if proximo == Pedidos.StatusPedido.FINALIZADO and pedido.forma_pagamento == Pedidos.FormaPagamento.DINHEIRO:
            if confirmacao != 'CONFIRMAR':
                # Retorna erro caso tentem burlar a segurança
                if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'erro', 'mensagem': 'Confirmação inválida.'}, status=400)
                return redirect('historico_pedidos')

        if proximo:
            pedido.status = proximo
            pedido.save()

            # Resposta de Sucesso para o JavaScript
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'status': 'sucesso', 'mensagem': 'Status avançado!'})

    return redirect('historico_pedidos')


from .models import (
    ProdutosMaisClick,
    PratoDiaMaisClick,
)
from django.views.generic import TemplateView
from django.utils.timezone import now
from django.db.models.functions import Coalesce
from django.db.models import Sum, Count, F, FloatField, ExpressionWrapper

from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.db.models import Sum, Count, Value, DecimalField, IntegerField, FloatField
from django.db.models.functions import Coalesce
from django.utils.timezone import now




class GerenciarPratosDiaView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        dias_semana = PratoDoDia.DIAS_SEMANA
        # Traz apenas produtos ativos. Se quiser filtrar só pela categoria "Pratos",
        # mude para: Produtos.objects.filter(ativo=True, categoria__nome_categoria="Pratos")
        produtos = Produtos.objects.filter(ativo=True).order_by('nome_produto')
        pratos_cadastrados = PratoDoDia.objects.filter(ativo=True).select_related('produto')

        agenda = []
        for num_dia, nome_dia in dias_semana:
            pratos_do_dia = pratos_cadastrados.filter(dia_semana=num_dia)
            agenda.append({
                'num_dia': num_dia,
                'nome_dia': nome_dia,
                'pratos': pratos_do_dia
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
        prato_dia.delete()  # ou prato_dia.ativo = False e depois prato_dia.save() se preferir soft delete
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



from django.db.models.functions import Coalesce, TruncDate, ExtractHour
from django.db.models import (
    Sum, Count, Value, DecimalField, IntegerField,
    ExpressionWrapper, F
)
from django.utils.timezone import now


class DashboardAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = "dashboards.html"
    login_url = "login"

    STATUS_LABELS = dict(Pedidos.StatusPedido.choices)
    PAGAMENTO_LABELS = dict(Pedidos.FormaPagamento.choices)

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

    def _get_datas_com_registro(self):
        return list(
            Pedidos.objects
            .exclude(criado_em__isnull=True)
            .annotate(data_registro=TruncDate("criado_em"))
            .values_list("data_registro", flat=True)
            .distinct()
            .order_by("data_registro")
        )

    def _get_datas(self):
        datas = [data for data in self._get_datas_com_registro() if data]

        if not datas:
            hoje = now().date()
            return {
                "data_inicio": hoje,
                "data_fim": hoje,
                "primeira_data_registro": None,
                "ultima_data_registro": None,
                "datas_com_registro": [],
            }

        primeira = datas[0]
        ultima = datas[-1]

        inicio = self._parse_data(self.request.GET.get("data_inicio")) or primeira
        fim = self._parse_data(self.request.GET.get("data_fim")) or ultima

        inicio = max(primeira, min(inicio, ultima))
        fim = max(primeira, min(fim, ultima))

        if inicio > fim:
            inicio, fim = primeira, ultima

        return {
            "data_inicio": inicio,
            "data_fim": fim,
            "primeira_data_registro": primeira,
            "ultima_data_registro": ultima,
            "datas_com_registro": [data.strftime("%Y-%m-%d") for data in datas],
        }

    def _serie_datas_completa(self, data_inicio, data_fim, vendas_por_data):
        mapa = {
            item["data"]: {
                "pedidos": item["pedidos"],
                "receita": float(item["receita"] or 0),
            }
            for item in vendas_por_data
        }

        labels = []
        pedidos = []
        receita = []

        dia = data_inicio
        while dia <= data_fim:
            labels.append(dia.strftime("%d/%m"))
            valores = mapa.get(dia, {"pedidos": 0, "receita": 0})
            pedidos.append(valores["pedidos"])
            receita.append(valores["receita"])
            dia += timedelta(days=1)

        return {
            "labels": labels,
            "pedidos": pedidos,
            "receita": receita,
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

        pedidos_validos = pedidos_periodo.exclude(
            status=Pedidos.StatusPedido.CANCELADO
        )

        itens_validos = (
            ItensPedido.objects
            .filter(pedidos__in=pedidos_validos)
            .select_related("produto")
            .distinct()
        )

        receita_total = pedidos_validos.aggregate(
            valor=Coalesce(
                Sum("total"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["valor"]

        taxas_entrega = pedidos_validos.aggregate(
            valor=Coalesce(
                Sum("taxa_motoca"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["valor"]

        total_pedidos = pedidos_validos.count()
        pedidos_cancelados = pedidos_periodo.filter(
            status=Pedidos.StatusPedido.CANCELADO
        ).count()

        ticket_medio = (
            receita_total / total_pedidos
            if total_pedidos
            else Decimal("0.00")
        )

        unidades_vendidas = itens_validos.aggregate(
            valor=Coalesce(
                Sum("quantidade"),
                Value(0),
                output_field=IntegerField(),
            )
        )["valor"]

        produtos_distintos = (
            itens_validos
            .exclude(produto__isnull=True)
            .values("produto_id")
            .distinct()
            .count()
        )

        ranking_produtos = list(
            itens_validos
            .values("produto__nome_produto")
            .annotate(
                total_quantidade=Coalesce(
                    Sum("quantidade"),
                    Value(0),
                    output_field=IntegerField(),
                ),
                total_receita=Coalesce(
                    Sum("subtotal"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            )
            .order_by("-total_quantidade", "-total_receita", "produto__nome_produto")
        )

        for item in ranking_produtos:
            item["total_receita_formatada"] = self._format_money(item["total_receita"])

        produto_lider = (
            ranking_produtos[0]["produto__nome_produto"]
            if ranking_produtos else "Sem vendas"
        )
        produto_lider_quantidade = (
            ranking_produtos[0]["total_quantidade"]
            if ranking_produtos else 0
        )

        vendas_diarias_qs = list(
            pedidos_validos
            .annotate(data=TruncDate("criado_em"))
            .values("data")
            .annotate(
                pedidos=Count("id"),
                receita=Coalesce(
                    Sum("total"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            )
            .order_by("data")
        )

        status_qs = list(
            pedidos_periodo
            .values("status")
            .annotate(total=Count("id"))
            .order_by("status")
        )

        pagamentos_qs = list(
            pedidos_validos
            .values("forma_pagamento")
            .annotate(total=Count("id"))
            .order_by("forma_pagamento")
        )

        entregas = pedidos_validos.filter(entrega=True).count()
        retiradas = pedidos_validos.filter(entrega=False).count()

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

        clicks_qs = ProdutosMaisClick.objects.select_related("produto")
        if datas["primeira_data_registro"]:
            clicks_qs = clicks_qs.filter(
                criacao__date__range=(data_inicio, data_fim)
            )

        produtos_click_todos = [
            {
                "produto": {
                    "nome_produto": (
                        item.produto.nome_produto
                        if item.produto else "Produto removido"
                    )
                },
                "quantidade": item.quantidade,
            }
            for item in clicks_qs.order_by("-quantidade", "produto__nome_produto")
        ]

        top_quantidade = ranking_produtos[:10]
        top_receita = sorted(
            ranking_produtos,
            key=lambda item: item["total_receita"],
            reverse=True,
        )[:10]
        top_clicks = produtos_click_todos[:10]

        return {
            **datas,

            "receita_total": self._format_money(receita_total),
            "taxas_entrega": self._format_money(taxas_entrega),
            "total_pedidos": total_pedidos,
            "pedidos_cancelados": pedidos_cancelados,
            "ticket_medio": self._format_money(ticket_medio),
            "unidades_vendidas": unidades_vendidas,
            "produtos_distintos": produtos_distintos,
            "total_entregas": entregas,
            "total_retiradas": retiradas,
            "produto_lider": produto_lider,
            "produto_lider_quantidade": produto_lider_quantidade,
            "horario_pico": horario_pico,
            "horario_pico_pedidos": horario_pico_pedidos,

            "mais_vendidos": ranking_produtos[:5],
            "mais_vendidos_todos": ranking_produtos,
            "produtos_click": produtos_click_todos[:5],
            "produtos_click_todos": produtos_click_todos,

            "grafico_vendas_diarias": self._serie_datas_completa(
                data_inicio,
                data_fim,
                vendas_diarias_qs,
            ),
            "grafico_status": {
                "labels": [
                    self.STATUS_LABELS.get(item["status"], item["status"])
                    for item in status_qs
                ],
                "valores": [item["total"] for item in status_qs],
            },
            "grafico_pagamentos": {
                "labels": [
                    self.PAGAMENTO_LABELS.get(
                        item["forma_pagamento"],
                        item["forma_pagamento"],
                    )
                    for item in pagamentos_qs
                ],
                "valores": [item["total"] for item in pagamentos_qs],
            },
            "grafico_entrega": {
                "labels": ["Entrega", "Retirada"],
                "valores": [entregas, retiradas],
            },
            "grafico_horarios": {
                "labels": [f"{hora:02d}h" for hora in range(24)],
                "valores": [horarios_map.get(hora, 0) for hora in range(24)],
            },
            "grafico_top_produtos": {
                "titulo": "Unidades",
                "labels": [
                    item["produto__nome_produto"] or "Produto removido"
                    for item in reversed(top_quantidade)
                ],
                "valores": [
                    item["total_quantidade"]
                    for item in reversed(top_quantidade)
                ],
            },
            "grafico_top_receita": {
                "titulo": "Receita",
                "labels": [
                    item["produto__nome_produto"] or "Produto removido"
                    for item in reversed(top_receita)
                ],
                "valores": [
                    float(item["total_receita"])
                    for item in reversed(top_receita)
                ],
            },
            "grafico_cliques": {
                "titulo": "Cliques",
                "labels": [
                    item["produto"]["nome_produto"]
                    for item in reversed(top_clicks)
                ],
                "valores": [
                    item["quantidade"]
                    for item in reversed(top_clicks)
                ],
            },
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_dashboard_data())
        return context

    def get(self, request, *args, **kwargs):
        if request.GET.get("ajax") == "1":
            return JsonResponse(self._get_dashboard_data(), safe=False)

        return super().get(request, *args, **kwargs)
