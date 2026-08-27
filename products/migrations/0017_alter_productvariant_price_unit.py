# Generated manually for the ProductVariant selling-unit vocabulary extension.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0016_private_inventory_import_source_storage"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productvariant",
            name="price_unit",
            field=models.CharField(
                choices=[
                    ("unit", "Per Unit"),
                    ("box", "Per Box"),
                    ("m2", "Per m²"),
                    ("tile", "Per Tile"),
                    ("bag", "Per Bag"),
                    ("bucket", "Per Bucket"),
                    ("pack", "Per Pack"),
                ],
                default="box",
                max_length=10,
            ),
        ),
    ]
