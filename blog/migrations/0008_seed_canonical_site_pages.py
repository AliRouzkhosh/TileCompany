from django.db import migrations


CANONICAL_SITE_PAGE_KEYS = (
    "about",
    "privacy",
    "terms",
    "shipping-delivery",
    "returns-refunds",
    "payment-information",
)


def create_canonical_site_pages(apps, schema_editor):
    SitePage = apps.get_model("blog", "SitePage")
    for key in CANONICAL_SITE_PAGE_KEYS:
        SitePage.objects.get_or_create(key=key)


def preserve_canonical_site_pages(apps, schema_editor):
    # Deliberately retain rows on rollback: operators may have added real copy.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0007_sitepage"),
    ]

    operations = [
        migrations.RunPython(
            create_canonical_site_pages,
            preserve_canonical_site_pages,
        ),
    ]
