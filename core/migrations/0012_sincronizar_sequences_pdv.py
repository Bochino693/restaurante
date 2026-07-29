from django.db import migrations


def sincronizar_sequences_pdv(apps, schema_editor):
    """
    Sincroniza as sequences PostgreSQL com o maior ID já existente.

    Corrige o erro:
    duplicate key value violates unique constraint "core_pedidos_itens_pkey"
    """
    connection = schema_editor.connection

    if connection.vendor != "postgresql":
        return

    Pedidos = apps.get_model("core", "Pedidos")
    ItensPedido = apps.get_model("core", "ItensPedido")
    RelacaoPedidoItens = Pedidos._meta.get_field("itens").remote_field.through

    modelos = [Pedidos, ItensPedido, RelacaoPedidoItens]
    quote = connection.ops.quote_name

    with connection.cursor() as cursor:
        for modelo in modelos:
            tabela = modelo._meta.db_table
            coluna_pk = modelo._meta.pk.column

            cursor.execute(
                "SELECT pg_get_serial_sequence(%s, %s)",
                [tabela, coluna_pk],
            )
            sequence = cursor.fetchone()[0]

            if not sequence:
                continue

            cursor.execute(
                f"SELECT COALESCE(MAX({quote(coluna_pk)}), 0) "
                f"FROM {quote(tabela)}"
            )
            maior_id = int(cursor.fetchone()[0] or 0)

            if maior_id > 0:
                cursor.execute(
                    "SELECT setval(%s, %s, true)",
                    [sequence, maior_id],
                )
            else:
                cursor.execute(
                    "SELECT setval(%s, 1, false)",
                    [sequence],
                )


def reverso(apps, schema_editor):
    # Não há motivo para voltar uma sequence para um valor antigo.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_reparar_relacao_pedidos_itens"),
    ]

    operations = [
        migrations.RunPython(
            sincronizar_sequences_pdv,
            reverse_code=reverso,
        ),
    ]