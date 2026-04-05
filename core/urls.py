from django.contrib import admin
from django.urls import path, include
from .views import (CaixaView, EstoqueView,
                    PedidosView, CardapioClienteView,
                    adicionais_produto, VendasView, DashboardAnalyticsView,
                    LoginView, LogoutView)

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


    path("logout/", LogoutView.as_view(), name="logout"),
    path('login/', LoginView.as_view(), name='login'),
]
