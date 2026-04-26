from django.contrib import admin
from django.urls import path, include
from .views import (CaixaView, EstoqueView,
                    PedidosView, CardapioClienteView,
                    adicionais_produto, VendasView, DashboardAnalyticsView,
                    LoginView, LogoutView, GerenciarPratosDiaView, RemoverPratoDiaView, avancar_status,
                    confirmar_impressao, pedidos_pendentes_impressao)

urlpatterns = [
    path('', CardapioClienteView.as_view(), name='cardapio'),
    path('caixa/', CaixaView.as_view(), name='caixa'),
    path('estoque/', EstoqueView.as_view(), name='estoque'),
    path('pedidos/', PedidosView.as_view(), name='pedidos'),
    path(
        "produto/<int:produto_id>/adicionais/",
        adicionais_produto,
    ),

    path('vendas/', VendasView.as_view(), name='vendas'),
    path('dashboards/', DashboardAnalyticsView.as_view(), name='dashboards'),
    path('admin-cardapio/pratos-do-dia/', GerenciarPratosDiaView.as_view(), name='gerenciar_pratos_dia'),
    path('pedido/<int:pedido_id>/avancar-status/', avancar_status, name='avancar_status'),
    path('admin-cardapio/pratos-do-dia/remover/<int:pk>/', RemoverPratoDiaView.as_view(), name='remover_prato_dia'),


    path('api/pedidos-impressao/', pedidos_pendentes_impressao),
    path('api/confirmar-impressao/<int:pedido_id>/', confirmar_impressao),
    path('pedidos/resumo/', ResumoPedidosView.as_view(), name='resumo_pedidos'),


    path("logout/", LogoutView.as_view(), name="logout"),
    path('login/', LoginView.as_view(), name='login'),
]
