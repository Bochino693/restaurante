from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
import os


class Command(BaseCommand):
    help = "Cria superusuário automaticamente"

    def handle(self, *args, **options):
        User = get_user_model()

        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if not username or not password:
            self.stdout.write(self.style.ERROR("Variáveis do superuser não definidas."))
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING("Superuser já existe."))
            return

        User.objects.create_superuser(
            username=username,
            email=email or "",
            password=password
        )

        self.stdout.write(self.style.SUCCESS("Superuser criado com sucesso!"))
