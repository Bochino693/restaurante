from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # O cardápio público fica 60 s em cache; mudou produto, preço,
        # categoria, adicional ou prato do dia, o cache sai na hora.
        from django.db.models.signals import m2m_changed, post_delete, post_save

        from .models import Adicional, CategoriaProdutos, PratoDoDia, Produtos
        from .views import _limpar_cache_cardapio

        def limpar(**kwargs):
            _limpar_cache_cardapio()

        for modelo in (Produtos, CategoriaProdutos, Adicional, PratoDoDia):
            post_save.connect(limpar, sender=modelo, dispatch_uid=f"cardapio-save-{modelo.__name__}")
            post_delete.connect(limpar, sender=modelo, dispatch_uid=f"cardapio-del-{modelo.__name__}")
        m2m_changed.connect(limpar, sender=Adicional.produtos.through, dispatch_uid="cardapio-m2m-adicional")
