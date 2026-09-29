from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_indices_pedidos"),
    ]

    operations = [
        migrations.AddField(
            model_name="pedidos",
            name="telefone",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
