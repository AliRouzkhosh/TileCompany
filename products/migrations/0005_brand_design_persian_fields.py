from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0004_product_attribute_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="brand",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="category",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="color",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="design",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="finish",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="material",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="product",
            name="description_fa",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="product",
            name="name_fa",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="product",
            name="series_name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="size",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="usage",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RemoveField(
            model_name="product",
            name="brand",
        ),
        migrations.RemoveField(
            model_name="product",
            name="design",
        ),
        migrations.AddField(
            model_name="product",
            name="brand",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="products",
                to="products.brand",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="designs",
            field=models.ManyToManyField(
                blank=True,
                related_name="products",
                to="products.design",
            ),
        ),
    ]
