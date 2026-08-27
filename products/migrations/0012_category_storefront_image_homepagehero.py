from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0011_alter_productvariant_price_unit"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="storefront_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="storefront/categories/",
            ),
        ),
        migrations.CreateModel(
            name="HomepageHero",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(default="primary", editable=False, max_length=32, unique=True)),
                ("image", models.ImageField(blank=True, null=True, upload_to="storefront/homepage/")),
                ("image_alt", models.CharField(blank=True, max_length=200)),
                ("image_alt_fa", models.CharField(blank=True, max_length=200)),
                ("is_enabled", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Homepage hero",
                "verbose_name_plural": "Homepage hero",
                "constraints": [models.CheckConstraint(condition=models.Q(("key", "primary")), name="homepage_hero_primary_key_only")],
            },
        ),
    ]
