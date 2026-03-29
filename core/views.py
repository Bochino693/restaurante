from datetime import date

from django.db.models import Sum
from django.shortcuts import render, get_object_or_404
from .models import Produtos, EstoqueProdutos, CategoriaProdutos, Pedidos, ItensPedido
from django.views.generic import View
import json
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone


class CardapioClienteView(View):

    def get(self, request):

        categorias = CategoriaProdutos.objects.prefetch_related(
            'produtos'
        ).filter(ativo=True)

        return render(request, 'index.html', {
            'categorias': categorias
        })


class CaixaView(View):
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


class EstoqueView(View):

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


class PedidosView(View):

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
