from django.db import migrations, models
import django_ckeditor_5.fields


class Migration(migrations.Migration):

    dependencies = [
        ("blog", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="article",
            name="content_fa",
            field=django_ckeditor_5.fields.CKEditor5Field(
                blank=True,
                config_name="default",
            ),
        ),
        migrations.AddField(
            model_name="article",
            name="meta_description_fa",
            field=models.CharField(blank=True, max_length=320),
        ),
        migrations.AddField(
            model_name="article",
            name="meta_title_fa",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="article",
            name="summary_fa",
            field=models.TextField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="article",
            name="title_fa",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="blogcategory",
            name="description_fa",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="blogcategory",
            name="name_fa",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="blogtag",
            name="name_fa",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
