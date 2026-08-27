from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticketcategory",
            name="description_fa",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="ticketcategory",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
    ]
