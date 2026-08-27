from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Repair SQLite databases left pointing at a retired Product table."""

    dependencies = [
        ("orders", "0001_initial"),
        ("products", "0006_remove_product_low_stock_threshold_and_more"),
    ]

    operations = [
        # SQLite rebuilds the table for AlterField. Removing then restoring the
        # constraint preserves rows while replacing the stale FK target
        # (products_product_old_bilingual) with products_product.
        migrations.AlterField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="products.product",
            ),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="products.product",
            ),
        ),
    ]
