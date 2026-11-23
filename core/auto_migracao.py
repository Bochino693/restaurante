"""Aplica as migrações pendentes quando o site sobe na Vercel.

Sem isto, uma atualização que cria tabelas (ex.: a 0015, das fotos no
banco) só funciona depois de alguém rodar `python manage.py migrate`
contra o banco de produção; até lá, enviar foto ou abrir "Restaurar fotos"
dava erro ("relation core_arquivonobanco does not exist").

- Só roda no site no ar (MIGRAR_AO_INICIAR, ligado por padrão na Vercel,
  fora do build).
- No Postgres, uma trava (pg_advisory_xact_lock) garante que só uma
  instância migra por vez; as outras esperam e encontram tudo aplicado.
  A trava é da transação, o que funciona no pooler do Supabase (porta 6543).
- O `migrate` dispara o post_migrate de sempre: as fotos antigas com cópia
  no projeto voltam para o banco (core.restauracao.restaurar_ao_migrar).
- Se algo falhar, registra no log e o site continua subindo.
"""
import logging

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.executor import MigrationExecutor

logger = logging.getLogger(__name__)

CHAVE_TRAVA = 23112026  # número fixo da trava de migração no Postgres


def pendentes(conexao):
    executor = MigrationExecutor(conexao)
    return executor.migration_plan(executor.loader.graph.leaf_nodes())


def migrar_pendentes(forcar=False):
    """Devolve a lista de migrações aplicadas (vazia se não havia nada).
    Nunca levanta exceção: o site sobe de qualquer jeito."""
    if not (forcar or getattr(settings, "MIGRAR_AO_INICIAR", False)):
        return []

    from django.core.management import call_command

    conexao = None
    aplicadas = []
    try:
        conexao = connections[DEFAULT_DB_ALIAS]
        plano = pendentes(conexao)
        if not plano:
            return []

        if conexao.vendor == "postgresql":
            with transaction.atomic(using=DEFAULT_DB_ALIAS):
                with conexao.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CHAVE_TRAVA])
                plano = pendentes(conexao)  # outra instância pode ter migrado enquanto esperávamos
                if plano:
                    call_command("migrate", interactive=False, verbosity=1)
        else:
            call_command("migrate", interactive=False, verbosity=1)

        aplicadas = [f"{m.app_label}.{m.name}" for m, _volta in plano]
        if aplicadas:
            logger.warning("Migrações aplicadas ao iniciar: %s", ", ".join(aplicadas))
    except Exception:
        logger.exception("Não foi possível aplicar as migrações ao iniciar; rode `python manage.py migrate`.")
        aplicadas = []
    finally:
        if conexao is not None:
            try:
                conexao.close()
            except Exception:
                pass
    return aplicadas
