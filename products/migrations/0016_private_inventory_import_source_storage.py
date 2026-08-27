# Generated manually for the private storage backend; no database column changes.
from django.db import migrations, models
import products.storage


class Migration(migrations.Migration):
    dependencies = [("products", "0015_productoperationalchange")]

    operations = [
        migrations.AlterField(
            model_name="inventorypriceimportbatch",
            name="source_file",
            field=models.FileField(blank=True, storage=products.storage.private_inventory_import_storage, upload_to="products/inventory-price-imports/%Y/%m/"),
        ),
    ]
