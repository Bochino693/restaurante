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
            total = data.get("total", 0)
            taxa_motoca = data.get("taxa_motoca", 0)  # ← movido para cá, antes de usar
            metodo_pagamento = data.get("metodo_pagamento")
            tipo_entrega = data.get("tipo_entrega")
            nome_cliente = data.get("nome_cliente")

            endereco = data.get("endereco", {})
            cep = endereco.get("cep")
            rua = endereco.get("rua")
            numero = endereco.get("numero")

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

            if tipo_entrega == "entrega" and (not cep or not rua or not numero):
                return JsonResponse(
                    {"success": False, "message": "CEP, rua e número são obrigatórios para entrega."},
                    status=400
                )

            with transaction.atomic():
                pedido = Pedidos.objects.create(
                    nome_cliente=nome_cliente,
                    total=total,
                    taxa_motoca=taxa_motoca,  # ← salvo direto no create
                    forma_pagamento=metodo_pagamento.upper(),
                    entrega=True if tipo_entrega == "entrega" else False,
                    cep=cep,
                    rua=rua,
                    numero=numero,
                    impresso=True  # ← marca como impresso ao finalizar
                )

                itens = []

                for item in carrinho:
                    produto = Produtos.objects.get(id=item["id"])

                    preco_base = float(item["precoBase"])
                    adicionais = item.get("adicionais", [])
                    soma_adicionais = sum(float(a["preco"]) for a in adicionais)

                    preco_unitario = preco_base + soma_adicionais
                    subtotal_item = preco_unitario * int(item["qtd"])

                    item_pedido = ItensPedido.objects.create(
                        produto=produto,
                        quantidade=item["qtd"],
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

        # Base de pedidos de hoje que não foram cancelados
        pedidos_hoje = Pedidos.objects.filter(criado_em__date=hoje).exclude(status=Pedidos.StatusPedido.CANCELADO)

        # Cálculo dos Totais
        total_dia = pedidos_hoje.aggregate(total=Sum('total'))['total'] or 0
        total_dinheiro = pedidos_hoje.filter(forma_pagamento=Pedidos.FormaPagamento.DINHEIRO).aggregate(total=Sum('total'))['total'] or 0
        total_pix = pedidos_hoje.filter(forma_pagamento=Pedidos.FormaPagamento.PIX).aggregate(total=Sum('total'))['total'] or 0
        total_cartao = pedidos_hoje.filter(forma_pagamento=Pedidos.FormaPagamento.CARTAO).aggregate(total=Sum('total'))['total'] or 0

        return render(request, 'pedidos.html', {
            'pedidos': pedidos,
            'total_dia': f"{total_dia:.2f}".replace('.', ','),
            'total_dinheiro': f"{total_dinheiro:.2f}".replace('.', ','),
            'total_pix': f"{total_pix:.2f}".replace('.', ','),
            'total_cartao': f"{total_cartao:.2f}".replace('.', ','),
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

class DashboardAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = "dashboards.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        hoje = now()

        # Produtos mais clicados
        context["produtos_click"] = (
            ProdutosMaisClick.objects.select_related("produto")
            .order_by("-quantidade")[:5]
        )

        # Pratos do dia mais clicados
        context["pratos_click"] = (
            PratoDiaMaisClick.objects.select_related("prato")
            .order_by("-quantidade")[:5]
        )

        # Produtos mais vendidos
        context["mais_vendidos"] = (
            ItensPedido.objects.values("produto__nome_produto")
            .annotate(total=Sum("quantidade"))
            .order_by("-total")[:5]
        )

        # Receita total
        context["receita_total"] = (
            Pedidos.objects.aggregate(total=Sum("total"))["total"] or 0
        )

        # Total pedidos
        context["total_pedidos"] = Pedidos.objects.count()

        # Ticket médio
        pedidos = Pedidos.objects.aggregate(
            media=Sum("total") / Count("id")
        )
        context["ticket_medio"] = pedidos["media"] or 0

        # Pedidos por status
        context["pedidos_status"] = (
            Pedidos.objects.values("status")
            .annotate(total=Count("id"))
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
