from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orders", "0007_order_delivered_at")]

    operations = [
        migrations.CreateModel(
            name="RefundAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="orders/refunds/%Y/%m/")),
                ("original_filename", models.CharField(blank=True, max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("order", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="refund_attachments", to="orders.order")),
            ],
            options={"ordering": ["uploaded_at", "pk"]},
        ),
    ]
