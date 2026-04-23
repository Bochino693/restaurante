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
                        Decimal(str(a["preco"])) for a in adicionais
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


class PedidosView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        hoje = timezone.localdate()

        pedidos = (
            Pedidos.objects
            .prefetch_related('itens__produto')
            .order_by('-criado_em')
        )

        pedidos_hoje = (
            Pedidos.objects
            .filter(criado_em__date=hoje)
            .exclude(status=Pedidos.StatusPedido.CANCELADO)
        )

        # 1. Calculamos o total bruto das vendas e o total das taxas primeiro
        stats_dia = pedidos_hoje.aggregate(
            bruto=Sum('total'),
            taxas=Sum('taxa_motoca')
        )

        valor_bruto = stats_dia['bruto'] or 0
        valor_taxas = stats_dia['taxas'] or 0

        # 2. O total do dia para o estabelecimento é o Bruto - Taxas do Motoboy
        total_dia = valor_bruto - valor_taxas

        # Totais por forma de pagamento (Mantendo a lógica de soma do campo 'total')
        # Se quiser que estes totais também excluam a taxa, será necessário subtrair
        # a taxa correspondente a cada filtro.
        total_dinheiro = (
                pedidos_hoje
                .filter(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO)
                .aggregate(total=Sum('total'))['total'] or 0
        )
        total_pix = (
                pedidos_hoje
                .filter(forma_pagamento=Pedidos.FormaPagamento.PIX)
                .aggregate(total=Sum('total'))['total'] or 0
        )
        total_cartao = (
                pedidos_hoje
                .filter(forma_pagamento=Pedidos.FormaPagamento.CARTAO)
                .aggregate(total=Sum('total'))['total'] or 0
        )

        # Valor que deve ser repassado ou separado para os motoboys
        total_taxa_motoca = valor_taxas

        return render(request, 'pedidos.html', {
            'pedidos': pedidos,
            'total_dia': f"{total_dia:.2f}".replace('.', ','),
            'total_dinheiro': f"{total_dinheiro:.2f}".replace('.', ','),
            'total_pix': f"{total_pix:.2f}".replace('.', ','),
            'total_cartao': f"{total_cartao:.2f}".replace('.', ','),
            'total_taxa_motoca': f"{total_taxa_motoca:.2f}".replace('.', ','),
            'status_options': Pedidos.StatusPedido.choices,
            'pagamento_options': Pedidos.FormaPagamento.choices
        })


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

class DashboardAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = "dashboards.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        hoje = now()

        # Top 5 - Produtos mais clicados
        context["produtos_click"] = (
            ProdutosMaisClick.objects.select_related("produto")
            .order_by("-quantidade")[:5]
        )

        # Ranking geral - Produtos mais clicados
        context["produtos_click_todos"] = (
            ProdutosMaisClick.objects.select_related("produto")
            .order_by("-quantidade")
        )

        # Top 5 - Pratos do dia mais clicados
        context["pratos_click"] = (
            PratoDiaMaisClick.objects.select_related("prato")
            .order_by("-quantidade")[:5]
        )

        # Top 5 - Produtos mais vendidos
        context["mais_vendidos"] = (
            ItensPedido.objects
            .values("produto__nome_produto")
            .annotate(
                total=Coalesce(
                    Sum("quantidade"),
                    Value(0),
                    output_field=IntegerField()
                )
            )
            .order_by("-total")[:5]
        )

        # Ranking geral - Produtos mais vendidos
        context["mais_vendidos_todos"] = (
            ItensPedido.objects
            .values("produto__nome_produto")
            .annotate(
                total=Coalesce(
                    Sum("quantidade"),
                    Value(0),
                    output_field=IntegerField()
                )
            )
            .order_by("-total")
        )

        # Receita total
        context["receita_total"] = (
            Pedidos.objects.aggregate(
                total=Coalesce(
                    Sum("total"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )["total"] or Decimal("0.00")
        )

        # Total pedidos
        context["total_pedidos"] = Pedidos.objects.count()

        # Ticket médio
        context["ticket_medio"] = (
            Pedidos.objects.aggregate(
                media=Coalesce(
                    Sum("total") / Count("id", distinct=True),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=12, decimal_places=2)
                )
            )["media"] or Decimal("0.00")
        )

        # Pedidos por status
        context["pedidos_status"] = (
            Pedidos.objects.values("status")
            .annotate(total=Count("id"))
            .order_by("status")
        )

        return context


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
