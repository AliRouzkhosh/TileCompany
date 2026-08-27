from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_alter_order_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="checkout_attempt_token",
            field=models.UUIDField(blank=True, editable=False, help_text="Durable idempotency token for the checkout submission that created this order.", null=True, unique=True),
        ),
    ]
