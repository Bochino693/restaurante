from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.restauracao import espelhar


class Command(BaseCommand):
    help = (
        "Copia para o banco do site as fotos antigas que ainda estão na Cloudinary "
        "(banco/<nome antigo>) e aponta os campos para a cópia. Pode rodar quantas "
        "vezes quiser: o que já foi copiado é pulado. Se a Cloudinary responder "
        "401/\"disabled\", para e avisa."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limite", type=int, default=0, help="copia no máximo N fotos nesta rodada")
        parser.add_argument("--simular", action="store_true", help="baixa e confere, sem gravar nada")

    def handle(self, *args, **opcoes):
        if not (settings.CLOUDINARY_STORAGE or {}).get("CLOUD_NAME"):
            raise CommandError(
                "Defina CLOUDINARY_CLOUD_NAME (só o nome da conta basta: as fotos são "
                "baixadas pelo endereço público)."
            )
        from cloudinary_storage.storage import MediaCloudinaryStorage

        resultado = espelhar(MediaCloudinaryStorage(), limite=opcoes["limite"], simular=opcoes["simular"])

        for antigo, novo, usos in resultado.copiadas:
            self.stdout.write(f"  copiada: {antigo} -> {novo} ({usos} uso{'s' if usos != 1 else ''})")
        for nome in resultado.sumidas:
            self.stdout.write(self.style.WARNING(f"  não existe na Cloudinary: {nome}"))
        for nome, mensagem in resultado.erros:
            self.stdout.write(self.style.ERROR(f"  erro em {nome}: {mensagem}"))

        self.stdout.write(
            f"Copiadas: {len(resultado.copiadas)} · sem arquivo na Cloudinary: {len(resultado.sumidas)} · "
            f"erros: {len(resultado.erros)} · ainda por copiar: {resultado.restantes}"
        )
        if resultado.interrompido:
            raise CommandError(
                "A Cloudinary recusou o acesso e a cópia parou: "
                f"{resultado.interrompido}. Reative a conta (só o dono consegue) e rode de novo; "
                "o que já foi copiado não se repete. Enquanto isso, use `restaurar_midia`."
            )
