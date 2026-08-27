from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from accounts.models import Address
from dashboard.selectors.orders import (
    get_attention_queue,
    get_dashboard_statistics,
    get_fulfillment_queue,
    get_in_transit_queue,
)
from dashboard.services import hold_order, mark_as_delivered, mark_as_shipped, resume_order
from notifications.models import Notification, NotificationEvent
from orders.models import Order, OrderItem, OrderNote, OrderStatusHistory
from orders.services import (
    cancel_unpaid_order,
    confirm_cod_order,
    mark_cod_payment_collected,
    mark_order_paid,
)
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


class CashOnDeliveryLifecycleTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user("buyer", password="secret")
        self.staff = get_user_model().objects.create_user("staff", password="secret")
        self.staff.groups.add(Group.objects.get(name="Order Manager"))
        self.product = Product.objects.create(name="COD Tile", slug="cod-tile", sku="COD-TILE", is_active=True)
        self.variant = ProductVariant.objects.create(product=self.product, sku="COD-60", stock=20, is_active=True)
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("20.00"))
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("2000000"))
        self.address = Address.objects.create(user=self.user, first_name="Buyer", last_name="One", phone="123", country="IR", province="Tehran", city="Tehran", street_address="1 Tile Street")

    def order(self, *, method=Order.PaymentMethod.COD, status=Order.Status.PENDING, paid=False, reserve=False):
        order = Order.objects.create(user=self.user, full_name="Buyer One", address="1 Tile Street", city="Tehran", phone="123", payment_method=method, status=status, is_paid=paid, currency=Currency.USD, total_amount=Decimal("20.00"))
        if reserve:
            OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, unit_price=20, quantity=1)
            self.variant.stock -= 1
            self.variant.save(update_fields=["stock"])
        return order

    def test_cod_checkout_confirms_unpaid_and_clears_checkout_without_gateway(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["cart"] = {f"v_{self.variant.pk}": 1}
        session["checkout_shipping"] = {"address_id": self.address.id, "shipping_method": "standard", "delivery_slot": ""}
        session["checkout_payment"] = {"payment_method": Order.PaymentMethod.COD}
        session.save()

        self.client.get(reverse("orders:checkout_review"))
        token = self.client.session["checkout_attempt_token"]
        response = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})
        order = Order.objects.get(user=self.user)
        self.assertRedirects(response, reverse("orders:order_result", args=[order.id]))
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertFalse(order.is_paid)
        self.assertEqual(self.client.session.get("cart"), {})
        self.assertNotIn("pending_order_id", self.client.session)
        self.assertTrue(OrderStatusHistory.objects.filter(order=order, status=Order.Status.CONFIRMED).exists())
        self.assertTrue(OrderNote.objects.filter(order=order, operation_code="COD_CONFIRMED").exists())

    def test_online_checkout_remains_pending_until_gateway_confirmation(self):
        order = self.order(method=Order.PaymentMethod.MOCK)
        self.assertTrue(order.requires_online_payment)
        self.assertEqual(order.status, Order.Status.PENDING)
        mark_order_paid(order, "mock-ref", "Development gateway")
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertTrue(OrderStatusHistory.objects.filter(order=order, status=Order.Status.CONFIRMED).exists())

    def test_pending_online_order_never_intercepts_cod_checkout(self):
        existing = self.order(method=Order.PaymentMethod.MOCK)
        self.client.force_login(self.user)
        session = self.client.session
        session["cart"] = {f"v_{self.variant.pk}": 1}
        session["checkout_shipping"] = {"address_id": self.address.id, "shipping_method": "standard", "delivery_slot": ""}
        session["checkout_payment"] = {"payment_method": Order.PaymentMethod.COD}
        session.save()

        self.client.get(reverse("orders:checkout_review"))
        response = self.client.post(
            reverse("orders:checkout_review"),
            {"checkout_attempt_token": self.client.session["checkout_attempt_token"]},
        )

        created = Order.objects.exclude(pk=existing.pk).get()
        self.assertRedirects(response, reverse("orders:order_result", args=[created.id]))
        self.assertEqual(created.status, Order.Status.CONFIRMED)
        self.assertEqual(created.payment_method, Order.PaymentMethod.COD)

    def test_stale_command_cancels_online_orders_but_never_cod(self):
        mock = self.order(method=Order.PaymentMethod.MOCK, reserve=True)
        zarinpal = self.order(method=Order.PaymentMethod.ZARINPAL, reserve=True)
        cod = self.order(method=Order.PaymentMethod.COD, reserve=True)  # malformed legacy pending COD
        cutoff = timezone.now() - timedelta(hours=25)
        Order.objects.filter(pk__in=[mock.pk, zarinpal.pk, cod.pk]).update(created_at=cutoff)
        call_command("cancel_stale_orders", hours=24)
        mock.refresh_from_db(); zarinpal.refresh_from_db(); cod.refresh_from_db(); self.variant.refresh_from_db()
        self.assertEqual(mock.status, Order.Status.CANCELLED)
        self.assertEqual(zarinpal.status, Order.Status.CANCELLED)
        self.assertEqual(cod.status, Order.Status.PENDING)
        self.assertIsNone(cod.inventory_restored_at)
        self.assertEqual(self.variant.stock, 19)  # two online reservations were restored

    def test_cod_fulfillment_hold_and_attention_rules(self):
        cod = self.order(status=Order.Status.CONFIRMED)
        online = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.CONFIRMED)
        paid = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.CONFIRMED, paid=True)
        self.assertTrue(cod.can_ship)
        self.assertFalse(online.can_ship)
        self.assertTrue(paid.can_ship)
        self.assertIn(cod, get_fulfillment_queue())
        self.assertNotIn(cod, get_attention_queue())
        self.assertIn(online, get_attention_queue())
        self.assertTrue(hold_order(cod, self.staff))
        cod.refresh_from_db(); self.assertEqual(cod.status, Order.Status.ON_HOLD)
        self.assertTrue(resume_order(cod, self.staff))

    def test_cod_customer_cancellation_restores_once_but_shipped_cod_cannot_cancel(self):
        cod = self.order(status=Order.Status.CONFIRMED, reserve=True)
        self.assertTrue(cod.can_cancel)
        result = cancel_unpaid_order(order=cod, actor=self.user)
        cod.refresh_from_db(); self.variant.refresh_from_db()
        self.assertTrue(result.restored)
        self.assertEqual(cod.status, Order.Status.CANCELLED)
        self.assertEqual(self.variant.stock, 20)
        self.assertFalse(cancel_unpaid_order(order=cod, actor=self.user).restored)
        shipped = self.order(status=Order.Status.SHIPPED)
        self.assertFalse(shipped.can_cancel)
        self.assertIsNone(cancel_unpaid_order(order=shipped, actor=self.user))

    def test_cod_collection_preserves_shipped_or_delivered_status_and_is_idempotent(self):
        order = self.order(status=Order.Status.SHIPPED)
        mark_cod_payment_collected(order, payment_reference="cash-42", actor=self.staff)
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.SHIPPED)
        self.assertEqual(order.payment_provider, "Cash on Delivery")
        self.assertEqual(order.payment_reference, "cash-42")
        self.assertIsNotNone(order.paid_at)
        mark_cod_payment_collected(order, payment_reference="different", actor=self.staff)
        self.assertEqual(OrderNote.objects.filter(order=order, operation_code="COD_PAYMENT_COLLECTED").count(), 1)
        order.status = Order.Status.DELIVERED; order.save(update_fields=["status"])
        mark_cod_payment_collected(order, payment_reference="different", actor=self.staff)
        order.refresh_from_db(); self.assertEqual(order.status, Order.Status.DELIVERED)

    def test_customer_payment_status_presentation_respects_cod_semantics(self):
        cod_confirmed = self.order(status=Order.Status.CONFIRMED)
        cod_shipped = self.order(status=Order.Status.SHIPPED)
        online_pending = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.PENDING)
        online_paid = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.CONFIRMED, paid=True)

        with override("fa"):
            self.assertEqual(str(cod_confirmed.payment_status_display), "پرداخت در محل")
            self.assertEqual(str(cod_shipped.payment_status_display), "پرداخت در محل")
            self.assertEqual(str(online_pending.payment_status_display), "در انتظار پرداخت")
            self.assertEqual(str(online_paid.payment_status_display), "پرداخت‌شده")

        self.assertFalse(cod_confirmed.requires_online_payment)
        self.assertTrue(online_pending.requires_online_payment)

    def test_delivery_collects_cod_payment_atomically_and_statistics_remain_financial(self):
        cod = self.order(status=Order.Status.CONFIRMED)
        self.assertTrue(mark_as_shipped(cod, self.staff))
        cod.refresh_from_db(); self.assertFalse(cod.is_paid)
        self.assertTrue(mark_as_delivered(cod, self.staff))
        cod.refresh_from_db()
        self.assertTrue(cod.is_paid)
        self.assertEqual(cod.status, Order.Status.DELIVERED)

        unpaid = self.order(status=Order.Status.CONFIRMED)
        statistics = get_dashboard_statistics()
        self.assertGreaterEqual(statistics["fulfillment_queue"], 1)
        self.assertGreaterEqual(statistics["paid_orders"], 1)
        self.assertFalse(unpaid.is_paid)
        self.assertFalse(unpaid.can_request_refund)
        unpaid.is_paid = True; unpaid.save(update_fields=["is_paid"])
        self.assertTrue(unpaid.can_request_refund)

    def test_in_transit_queue_contains_only_shipped_paid_and_unpaid_cod_orders(self):
        pending = self.order(status=Order.Status.PENDING)
        confirmed_paid = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.CONFIRMED, paid=True)
        confirmed_cod = self.order(status=Order.Status.CONFIRMED)
        on_hold = self.order(status=Order.Status.ON_HOLD)
        shipped_paid = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.SHIPPED, paid=True)
        shipped_cod = self.order(status=Order.Status.SHIPPED)
        delivered = self.order(status=Order.Status.DELIVERED)
        cancelled = self.order(status=Order.Status.CANCELLED)

        queue_ids = set(get_in_transit_queue().values_list("id", flat=True))

        self.assertEqual(queue_ids, {shipped_paid.id, shipped_cod.id})
        self.assertNotIn(pending.id, queue_ids)
        self.assertNotIn(confirmed_paid.id, queue_ids)
        self.assertNotIn(confirmed_cod.id, queue_ids)
        self.assertNotIn(on_hold.id, queue_ids)
        self.assertNotIn(delivered.id, queue_ids)
        self.assertNotIn(cancelled.id, queue_ids)
        self.assertFalse(shipped_cod.is_paid)

    def test_shipping_and_delivery_update_in_transit_queue_for_paid_and_cod_orders(self):
        paid = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.CONFIRMED, paid=True)
        cod = self.order(status=Order.Status.CONFIRMED)

        self.assertTrue(mark_as_shipped(paid, self.staff))
        self.assertTrue(mark_as_shipped(cod, self.staff))
        self.assertCountEqual(get_in_transit_queue(), [paid, cod])

        self.assertTrue(mark_as_delivered(paid, self.staff))
        self.assertTrue(mark_as_delivered(cod, self.staff))
        paid.refresh_from_db()
        cod.refresh_from_db()
        self.assertEqual(paid.status, Order.Status.DELIVERED)
        self.assertEqual(cod.status, Order.Status.DELIVERED)
        self.assertTrue(cod.is_paid)
        self.assertFalse(get_in_transit_queue().exists())

    def test_in_transit_statistics_match_shipped_queue(self):
        paid = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.SHIPPED, paid=True)
        cod = self.order(status=Order.Status.SHIPPED)
        self.order(status=Order.Status.CONFIRMED)
        self.order(status=Order.Status.DELIVERED)
        self.order(status=Order.Status.CANCELLED)

        self.assertEqual(get_dashboard_statistics()["in_transit_orders"], 2)
        self.assertCountEqual(get_in_transit_queue(), [paid, cod])

    def test_dashboard_delivery_action_removes_in_transit_order_and_notifies_once(self):
        order = self.order(status=Order.Status.SHIPPED)
        self.client.force_login(self.staff)

        response = self.client.post(reverse("dashboard:deliver_order", args=[order.id]))

        self.assertRedirects(response, reverse("dashboard:order_detail", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertFalse(get_in_transit_queue().filter(pk=order.pk).exists())
        self.assertEqual(
            Notification.objects.filter(
                user=order.user,
                event_type=NotificationEvent.ORDER_DELIVERED,
            ).count(),
            1,
        )

    def test_staff_can_render_localized_in_transit_queue_but_customer_cannot(self):
        self.order(status=Order.Status.SHIPPED)
        self.client.force_login(self.staff)

        for language in ("en", "fa"):
            with override(language):
                response = self.client.get(reverse("dashboard:in_transit_queue"))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "In Transit" if language == "en" else "در مسیر")

        self.client.force_login(self.user)
        self.assertNotEqual(
            self.client.get(reverse("dashboard:in_transit_queue")).status_code,
            200,
        )
