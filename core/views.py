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



class DashboardAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = "dashboards.html"

    def _format_money(self, valor):
        return (
            f"{valor or Decimal('0.00'):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    def _get_datas(self):
        hoje = now().date()

        data_inicio = self.request.GET.get("data_inicio")
        data_fim = self.request.GET.get("data_fim")

        try:
            data_inicio = datetime.strptime(data_inicio, "%Y-%m-%d").date() if data_inicio else hoje - timedelta(days=30)
            data_fim = datetime.strptime(data_fim, "%Y-%m-%d").date() if data_fim else hoje
        except Exception:
            data_inicio = hoje - timedelta(days=30)
            data_fim = hoje

        return data_inicio, data_fim

    def _get_dashboard_data(self):
        data_inicio, data_fim = self._get_datas()

        pedidos = Pedidos.objects.filter(
            criado_em__date__range=[data_inicio, data_fim]
        )

        itens_pedido = ItensPedido.objects.filter(
            pedidos__criado_em__date__range=[data_inicio, data_fim]
        ).distinct()

        mais_vendidos_qs = (
            itens_pedido
            .values("produto__nome_produto")
            .annotate(total=Coalesce(Sum("quantidade"), Value(0), output_field=IntegerField()))
            .order_by("-total")
        )

        produtos_click_qs = (
            ProdutosMaisClick.objects
            .select_related("produto")
            .order_by("-quantidade")
        )

        receita_total = pedidos.aggregate(
            total=Coalesce(
                Sum("total"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )["total"]

        total_pedidos = pedidos.count()

        ticket_medio = Decimal("0.00")
        if total_pedidos > 0:
            ticket_medio = receita_total / total_pedidos

        pedidos_status = list(
            pedidos.values("status")
            .annotate(total=Count("id"))
            .order_by("status")
        )

        mais_vendidos_todos = list(mais_vendidos_qs)
        mais_vendidos = mais_vendidos_todos[:5]

        produtos_click_todos = [
            {
                "produto": {
                    "nome_produto": item.produto.nome_produto if item.produto else "Sem produto"
                },
                "quantidade": item.quantidade
            }
            for item in produtos_click_qs
        ]

        produtos_click = produtos_click_todos[:5]

        return {
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "receita_total": self._format_money(receita_total),
            "total_pedidos": total_pedidos,
            "ticket_medio": self._format_money(ticket_medio),
            "mais_vendidos": mais_vendidos,
            "mais_vendidos_todos": mais_vendidos_todos,
            "produtos_click": produtos_click,
            "produtos_click_todos": produtos_click_todos,
            "pedidos_status": pedidos_status,
            "produtos_vendidos_total": len(mais_vendidos_todos),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._get_dashboard_data())
        return context

    def get(self, request, *args, **kwargs):
        if request.GET.get("ajax") == "1":
            return JsonResponse(self._get_dashboard_data(), safe=False)

        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)

