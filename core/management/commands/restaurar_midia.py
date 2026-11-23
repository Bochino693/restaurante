from pathlib import Path

from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from core.midia import MidiaNoBanco
from core.restauracao import descrever, faltando, restaurar_caminhos, restaurar_copias_locais


class Command(BaseCommand):
    help = (
        "Traz de volta para o banco as fotos antigas. Sem opções: procura cópias "
        "confiáveis dentro do projeto (produtos/, imagens_categorias/, media/...). "
        "--zip: importa o ZIP baixado da Cloudinary, fotos soltas ou pastas. "
        "--relatorio: lista o que ainda falta. Pode rodar sempre (no deploy): não "
        "repete trabalho e nunca derruba nada."
    )

    def add_arguments(self, parser):
        parser.add_argument("--zip", nargs="+", metavar="ARQUIVO", help="ZIP da Cloudinary, fotos ou pastas")
        parser.add_argument("--relatorio", action="store_true", help="só lista as fotos que ainda faltam")
        parser.add_argument("--simular", action="store_true", help="mostra o que faria, sem gravar nada")

    def handle(self, *args, **opcoes):
        if opcoes["relatorio"]:
            self._listar_faltando()
            return

        if not opcoes["simular"] and not isinstance(storages["default"], MidiaNoBanco):
            aviso = (
                "O armazenamento padrão não é o banco: as fotos restauradas não apareceriam. "
                "Rode com o banco de produção (DATABASE_URL) ou com MIDIA_NO_BANCO=1. Nada foi alterado."
            )
            if opcoes["zip"]:
                raise CommandError(aviso)
            self.stdout.write(self.style.WARNING(aviso))  # no deploy, avisa e segue
            return

        if opcoes["zip"]:
            for caminho in opcoes["zip"]:
                if not Path(caminho).exists():
                    raise CommandError(f"Não achei {caminho}.")
            relatorio = restaurar_caminhos(opcoes["zip"], simular=opcoes["simular"])
        else:
            try:
                relatorio = restaurar_copias_locais(simular=opcoes["simular"])
            except Exception as erro:  # no deploy, nunca derrubar
                self.stderr.write(f"Restauração das cópias locais não rodou: {erro}")
                return

        for caminho, antigo, novo, usos in relatorio.restauradas:
            self.stdout.write(f"  restaurada: {caminho} -> {novo} (era {antigo}; {usos} uso{'s' if usos != 1 else ''})")
        for caminho, motivo in relatorio.duvidas:
            self.stdout.write(self.style.WARNING(f"  na dúvida, não usei: {caminho} ({motivo})"))
        for caminho, mensagem in relatorio.erros:
            self.stdout.write(self.style.ERROR(f"  erro em {caminho}: {mensagem}"))
        self.stdout.write(
            f"Restauradas: {len(relatorio.restauradas)} · já estavam no banco: {len(relatorio.ja_restauradas)} · "
            f"sem uso no site: {len(relatorio.nao_referenciados)} · na dúvida: {len(relatorio.duvidas)} · "
            f"erros: {len(relatorio.erros)} · ainda faltando: {len(faltando())}"
        )

    def _listar_faltando(self):
        linhas = descrever(faltando())
        if not linhas:
            self.stdout.write(self.style.SUCCESS("Nenhuma foto faltando: todas estão no banco."))
            return
        self.stdout.write(f"Fotos ainda faltando: {len(linhas)}")
        for linha in linhas:
            rotulo = f" ({linha['rotulo']})" if linha["rotulo"] else ""
            self.stdout.write(f"  {linha['modelo']} #{linha['pk']}{rotulo} · {linha['campo']} · {linha['nome']}")
