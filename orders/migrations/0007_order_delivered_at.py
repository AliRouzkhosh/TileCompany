from django.db import migrations, models


def backfill_delivered_at_from_history(apps, schema_editor):
    """Use delivery transition history only; legacy rows without it remain unknown."""

    Order = apps.get_model("orders", "Order")
    OrderStatusHistory = apps.get_model("orders", "OrderStatusHistory")

    for order in Order.objects.filter(status="delivered", delivered_at__isnull=True).iterator():
        delivered_history = (
            OrderStatusHistory.objects.filter(order_id=order.pk, status="delivered")
            .order_by("-changed_at", "-pk")
            .first()
        )
        if delivered_history:
            order.delivered_at = delivered_history.changed_at
            order.save(update_fields=["delivered_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0006_order_checkout_attempt_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_delivered_at_from_history, migrations.RunPython.noop),
    ]
