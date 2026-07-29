from django.db import migrations


CONSTRAINT_NAME = "core_pedidos_itens_pedido_item_uniq"


def reparar_relacao_pedidos_itens(apps, schema_editor):
    """
    Repara a tabela automática do ManyToMany Pedidos.itens.

    O problema observado no PostgreSQL/Supabase é uma restrição UNIQUE
    aplicada somente à coluna do pedido. Nesse cenário, o primeiro item
    entra e os demais são silenciosamente ignorados pelo método add() do
    Django, porque o PostgreSQL usa ON CONFLICT DO NOTHING na relação M2M.

    A correção:
    1. remove UNIQUE incorreto de apenas uma FK;
    2. elimina relações duplicadas exatas, caso existam;
    3. garante UNIQUE composto (pedido_id, item_id), que é o padrão correto.
    """
    connection = schema_editor.connection

    # No SQLite criado normalmente pelo Django, a tabela M2M já nasce correta.
    # A falha ocorreu no PostgreSQL/Supabase.
    if connection.vendor != "postgresql":
        return

    Pedidos = apps.get_model("core", "Pedidos")
    ItensPedido = apps.get_model("core", "ItensPedido")

    campo_m2m = Pedidos._meta.get_field("itens")
    modelo_relacao = campo_m2m.remote_field.through
    nome_tabela = modelo_relacao._meta.db_table

    campos_fk = [
        campo
        for campo in modelo_relacao._meta.fields
        if getattr(campo, "remote_field", None)
    ]

    campo_pedido = next(
        (
            campo
            for campo in campos_fk
            if (
                campo.remote_field.model._meta.label_lower
                == Pedidos._meta.label_lower
            )
        ),
        None
    )
    campo_item = next(
        (
            campo
            for campo in campos_fk
            if (
                campo.remote_field.model._meta.label_lower
                == ItensPedido._meta.label_lower
            )
        ),
        None
    )

    if not campo_pedido or not campo_item:
        raise RuntimeError(
            "Não foi possível identificar as colunas da relação "
            "Pedidos.itens."
        )

    coluna_pedido = campo_pedido.column
    coluna_item = campo_item.column
    coluna_pk = modelo_relacao._meta.pk.column

    quote = connection.ops.quote_name

    def obter_constraints():
        with connection.cursor() as cursor:
            return connection.introspection.get_constraints(
                cursor,
                nome_tabela
            )

    constraints = obter_constraints()

    # Remove UNIQUE de uma única coluna, que transforma indevidamente
    # a relação em "um item por pedido" ou "um pedido por item".
    for nome, dados in constraints.items():
        colunas = [
            coluna
            for coluna in (dados.get("columns") or [])
            if coluna
        ]

        unique_incorreto = (
            dados.get("unique")
            and not dados.get("primary_key")
            and len(colunas) == 1
            and colunas[0] in {coluna_pedido, coluna_item}
        )

        if not unique_incorreto:
            continue

        if dados.get("index"):
            schema_editor.execute(
                f"DROP INDEX IF EXISTS {quote(nome)}"
            )
        else:
            schema_editor.execute(
                f"ALTER TABLE {quote(nome_tabela)} "
                f"DROP CONSTRAINT IF EXISTS {quote(nome)}"
            )

    # Remove somente pares duplicados idênticos antes de criar/garantir
    # a restrição composta correta.
    schema_editor.execute(
        f"""
        DELETE FROM {quote(nome_tabela)} AS atual
        USING {quote(nome_tabela)} AS repetida
        WHERE atual.{quote(coluna_pk)} > repetida.{quote(coluna_pk)}
          AND atual.{quote(coluna_pedido)} = repetida.{quote(coluna_pedido)}
          AND atual.{quote(coluna_item)} = repetida.{quote(coluna_item)}
        """
    )

    constraints = obter_constraints()

    possui_unique_composto = any(
        dados.get("unique")
        and set(dados.get("columns") or [])
            == {coluna_pedido, coluna_item}
        and len(dados.get("columns") or []) == 2
        for dados in constraints.values()
    )

    if not possui_unique_composto:
        schema_editor.execute(
            f"ALTER TABLE {quote(nome_tabela)} "
            f"ADD CONSTRAINT {quote(CONSTRAINT_NAME)} "
            f"UNIQUE ({quote(coluna_pedido)}, {quote(coluna_item)})"
        )


def reverso(apps, schema_editor):
    # Não recriamos a restrição incorreta no rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_pedidos_descricao"),
    ]

    operations = [
        migrations.RunPython(
            reparar_relacao_pedidos_itens,
            reverse_code=reverso
        ),
    ]