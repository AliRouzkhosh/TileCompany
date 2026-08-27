from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0003_remove_product_category_product_categories"),
    ]

    operations = [
        migrations.CreateModel(
            name="Color",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(unique=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Finish",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(unique=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Material",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(unique=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="Size",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(unique=True)),
                ("length_mm", models.PositiveIntegerField(blank=True, null=True)),
                ("width_mm", models.PositiveIntegerField(blank=True, null=True)),
            ],
            options={
                "ordering": ["length_mm", "width_mm", "name"],
            },
        ),
        migrations.RemoveField(
            model_name="product",
            name="color",
        ),
        migrations.RemoveField(
            model_name="product",
            name="finish",
        ),
        migrations.RemoveField(
            model_name="product",
            name="length_mm",
        ),
        migrations.RemoveField(
            model_name="product",
            name="material",
        ),
        migrations.RemoveField(
            model_name="product",
            name="size",
        ),
        migrations.RemoveField(
            model_name="product",
            name="thickness_mm",
        ),
        migrations.RemoveField(
            model_name="product",
            name="usage",
        ),
        migrations.RemoveField(
            model_name="product",
            name="width_mm",
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="color",
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="finish",
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="material",
        ),
        migrations.RemoveField(
            model_name="productvariant",
            name="size",
        ),
        migrations.AddField(
            model_name="product",
            name="materials",
            field=models.ManyToManyField(blank=True, related_name="products", to="products.material"),
        ),
        migrations.AddField(
            model_name="product",
            name="usages",
            field=models.ManyToManyField(blank=True, related_name="products", to="products.usage"),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="color",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="variants", to="products.color"),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="finish",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="variants", to="products.finish"),
        ),
        migrations.AddField(
            model_name="productvariant",
            name="size",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="variants", to="products.size"),
        ),
    ]
