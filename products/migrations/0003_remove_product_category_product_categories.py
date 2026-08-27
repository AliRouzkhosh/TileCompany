# Generated manually for the development-only product category model change.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0002_remove_product_brand_ref_remove_product_categories_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="product",
            name="category",
        ),
        migrations.AddField(
            model_name="product",
            name="categories",
            field=models.ManyToManyField(
                blank=True,
                related_name="categorized_products",
                to="products.category",
            ),
        ),
    ]
