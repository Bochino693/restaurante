from datetime import date

from django.db.models import Sum, Count, Prefetch
from django.shortcuts import render, get_object_or_404
from .models import Produtos, EstoqueProdutos, CategoriaProdutos, Pedidos, ItensPedido, PratoDoDia, Adicional
from django.views.generic import View
import json
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.contrib.auth.mixins import LoginRequiredMixin


class CardapioClienteView(View):
    def get(self, request):
        # 1. Preparamos o filtro de produtos ativos para usar dentro das categorias
        produtos_ativos = Produtos.objects.filter(ativo=True).prefetch_related("adicionais_disponiveis")

        # 2. Filtra categorias que possuem produtos ativos e já traz os produtos filtrados
        categorias = CategoriaProdutos.objects.filter(
            ativo=True,
            produtos__ativo=True
        ).annotate(
            total_produtos=Count('produtos')
        ).filter(
            total_produtos__gt=0
        ).prefetch_related(
            Prefetch("produtos", queryset=produtos_ativos)  # GARANTE que só produtos ativos venham no loop
        ).distinct()

        # 3. Prato do dia
        dia_hoje = timezone.localdate().weekday()
        pratos_do_dia = PratoDoDia.objects.filter(
            dia_semana=dia_hoje,
            ativo=True,
            produto__ativo=True
        ).select_related("produto")

        # 4. Lógica de horário (Segunda a Sábado | 11h às 16h)
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
        produtos = Produtos.objects.all()
        categorias = CategoriaProdutos.objects.all()
        return render(request, 'caixa.html', {
            'produtos': produtos,
            'categorias': categorias,
        })

    def post(self, request):
        try:
            data = json.loads(request.body)
            carrinho = data.get('carrinho', [])
            total_pedido = data.get('total', 0)

            if not carrinho:
                return JsonResponse({'success': False, 'message': 'Carrinho vazio'}, status=400)

            with transaction.atomic():
                # 1. Cria o Pedido principal
                novo_pedido = Pedidos.objects.create(total=total_pedido)

                itens_para_adicionar = []

                for item in carrinho:
                    # Busca o produto no banco
                    produto_obj = Produtos.objects.get(id=item['id'])

                    # Calcula subtotal do item (Preço base + adicionais) * quantidade
                    preco_base = float(item['precoBase'])
                    soma_adicionais = sum(float(a['preco']) for a in item.get('adicionais', []))
                    preco_unitario_final = preco_base + soma_adicionais
                    subtotal_item = preco_unitario_final * int(item['qtd'])

                    # 2. Cria o objeto ItensPedido
                    item_pedido = ItensPedido.objects.create(
                        produto=produto_obj,
                        quantidade=item['qtd'],
                        preco_unitario=preco_unitario_final,
                        subtotal=subtotal_item,
                        adicionais=item.get('adicionais', [])  # Salva o JSON dos adicionais
                    )
                    itens_para_adicionar.append(item_pedido)

                # 3. Associa os itens ao pedido (ManyToMany)
                novo_pedido.itens.set(itens_para_adicionar)
                novo_pedido.save()

            return JsonResponse({'success': True, 'pedido_id': novo_pedido.id})

        except Produtos.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Produto não encontrado'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'message': str(e)}, status=500)


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

        context = {
            'estoque': estoque,
            'categorias': categorias
        }

        return render(request, 'estoque.html', context)


class PedidosView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        hoje = timezone.localdate()

        pedidos = (
            Pedidos.objects
            .prefetch_related('itens__produto')
            .order_by('-criado_em')
        )

        total_dia = (
                Pedidos.objects.filter(
                    criado_em__date=hoje
                )
                .exclude(
                    status=Pedidos.StatusPedido.CANCELADO
                )
                .aggregate(total=Sum('total'))
                ['total'] or 0
        )

        return render(request, 'pedidos.html', {
            'pedidos': pedidos,
            'total_dia': f"{total_dia:.2f}".replace('.', ','),
            'status_options': Pedidos.StatusPedido.choices
        })


class VendasView(LoginRequiredMixin, View):
    login_url = "login"

    def get(self, request):
        pedidos = Pedidos.objects.all()

        context = {
            'pedidos': pedidos,
        }

        return render(request, 'vendas.html', context)


class DashboardView(View):

    def get(self, request):
        pedidos = Pedidos.objects.all()

        context = {
            'pedidos': pedidos
        }

        return render(request, 'dashboard.html', context)

from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.contrib import messages


class LoginView(View):
    template_name = "login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("pedidos")  # ou outra tela inicial
        return render(request, self.template_name)

    def post(self, request):
        identificador = request.POST.get("identificador", "").strip()
        senha = request.POST.get("senha", "").strip()

        if not identificador or not senha:
            messages.error(request, "Preencha usuário/e-mail e senha.")
            return render(request, self.template_name)

        username_para_login = identificador

        # Se digitou email, tenta localizar o username correspondente
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
            return redirect("pedidos")  # troque para a rota inicial do painel
        else:
            messages.error(request, "Login ou senha inválidos.")
            return render(request, self.template_name)

from django.contrib.auth import logout


class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect("login")


