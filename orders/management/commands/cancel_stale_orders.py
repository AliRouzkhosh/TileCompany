#### orders/management/commands/cancel_stale_orders.py
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from orders.models import Order
from orders.services import cancel_unpaid_order


class Command(BaseCommand):
    help = "Cancels pending orders older than 24 hours and restores their reserved stock."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours", type=int, default=24,
            help="How many hours an order can stay pending before being auto-cancelled (default: 24)",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        cutoff = timezone.now() - timedelta(hours=hours)

        stale_orders = Order.objects.filter(
            status=Order.Status.PENDING,
            is_paid=False,
            payment_method__in=[Order.PaymentMethod.MOCK, Order.PaymentMethod.ZARINPAL],
            created_at__lt=cutoff,
        )
        count = 0

        for order in stale_orders:
            with transaction.atomic():
                result = cancel_unpaid_order(
                    order=order,
                    reason=f"online payment not completed within {hours} hours",
                    operation_code="STALE_ONLINE_PAYMENT_CANCELLED",
                )
            if result:
                count += 1
                self.stdout.write(self.style.SUCCESS(f"Cancelled stale Order #{order.id}"))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {count} stale order(s) cancelled."))
