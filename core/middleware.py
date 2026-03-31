from django.shortcuts import redirect
from django.urls import resolve


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

        self.rotas_livres = [
            "login",
            "cardapio",
            "adicionais_produto"
        ]

    def __call__(self, request):
        if not request.user.is_authenticated:
            rota_atual = resolve(request.path_info).url_name

            if rota_atual not in self.rotas_livres:
                return redirect("login")

        return self.get_response(request)

