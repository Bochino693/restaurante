from django.contrib import admin
from django.urls import path, include
from .views import CaixaView, EstoqueView, PedidosView, CardapioClienteView

urlpatterns = [
    path('', CardapioClienteView.as_view(), name='cardapio'),
    path('caixa/', CaixaView.as_view(), name='caixa'),
    path('estoque/', EstoqueView.as_view(), name='estoque'),
    path('pedidos/', PedidosView.as_view(), name='pedidos')
]
