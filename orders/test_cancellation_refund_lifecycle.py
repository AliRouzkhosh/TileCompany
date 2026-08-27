from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from dashboard.services import mark_as_delivered
from orders.models import Order, OrderItem
from orders.services import create_refund_request, mark_cod_payment_collected
from products.models import Product, ProductVariant


class CustomerCancellationAndRefundLifecycleTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(override("en"))
        self.user = get_user_model().objects.create_user("lifecycle-buyer", password="secret")
        self.staff = get_user_model().objects.create_user("lifecycle-staff", password="secret", is_staff=True)
        self.product = Product.objects.create(name="Lifecycle Tile", slug="lifecycle-tile", sku="LIFECYCLE-TILE")
        self.variant = ProductVariant.objects.create(product=self.product, sku="LIFECYCLE-60", stock=10)
        self.client.force_login(self.user)

    def order(self, *, method=Order.PaymentMethod.COD, status=Order.Status.CONFIRMED, paid=False, delivered_at=None, reserve=True):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            address="1 Tile Street",
            city="Tehran",
            phone="123",
            payment_method=method,
            status=status,
            is_paid=paid,
            delivered_at=delivered_at,
            total_amount=Decimal("20.00"),
        )
        if reserve:
            OrderItem.objects.create(
                order=order,
                product=self.product,
                variant=self.variant,
                product_name=self.product.name,
                unit_price=Decimal("20.00"),
                quantity=1,
            )
            self.variant.stock -= 1
            self.variant.save(update_fields=["stock"])
        return order

    def assert_action_visibility(self, order, *, cancel, refund):
        with override("en"):
            response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual("Cancel order" in response.content.decode(), cancel)
        self.assertEqual("Request refund" in response.content.decode(), refund)

    def test_confirmed_unpaid_cod_customer_can_see_and_cancel(self):
        order = self.order()
        self.assertTrue(order.can_cancel)
        self.assert_action_visibility(order, cancel=True, refund=False)

        response = self.client.post(reverse("orders:order_cancel", args=[order.pk]))
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(self.variant.stock, 10)
        self.assertIsNotNone(order.inventory_restored_at)

    def test_on_hold_unpaid_cod_customer_can_see_and_cancel(self):
        order = self.order(status=Order.Status.ON_HOLD)
        self.assertTrue(order.can_cancel)
        self.assert_action_visibility(order, cancel=True, refund=False)
        self.client.post(reverse("orders:order_cancel", args=[order.pk]))
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(self.variant.stock, 10)

    def test_shipped_cod_has_no_customer_cancel_or_refund(self):
        order = self.order(status=Order.Status.SHIPPED)
        self.assertFalse(order.can_cancel)
        self.assertFalse(order.can_request_refund)
        self.assert_action_visibility(order, cancel=False, refund=False)
        response = self.client.post(reverse("orders:order_cancel", args=[order.pk]))
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.SHIPPED)

    def test_online_pending_cancellation_and_paid_pre_shipment_refund_actions(self):
        pending = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.PENDING)
        self.assert_action_visibility(pending, cancel=True, refund=False)
        self.assertContains(self.client.get(reverse("orders:order_detail", args=[pending.pk])), "Complete payment")
        self.client.post(reverse("orders:order_cancel", args=[pending.pk]))
        pending.refresh_from_db()
        self.assertEqual(pending.status, Order.Status.CANCELLED)

        paid = self.order(method=Order.PaymentMethod.MOCK, paid=True)
        self.assert_action_visibility(paid, cancel=False, refund=True)
        response = self.client.post(reverse("orders:order_cancel", args=[paid.pk]), {"reason": Order.RefundReason.OTHER, "message": "Please refund"})
        self.assertRedirects(response, reverse("orders:order_detail", args=[paid.pk]))
        paid.refresh_from_db()
        self.assertEqual(paid.refund_status, Order.RefundStatus.REQUESTED)
        self.assertIsNone(paid.inventory_restored_at)

    def test_on_hold_paid_online_can_request_refund_but_shipped_cannot(self):
        held = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.ON_HOLD, paid=True)
        self.assert_action_visibility(held, cancel=False, refund=True)
        shipped = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.SHIPPED, paid=True)
        self.assert_action_visibility(shipped, cancel=False, refund=False)

    def test_delivered_refund_window_is_inclusive_and_requires_timestamp(self):
        now = timezone.now()
        online = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.DELIVERED, paid=True, delivered_at=now - timedelta(hours=71))
        cod = self.order(status=Order.Status.DELIVERED, paid=True, delivered_at=now - timedelta(hours=71))
        legacy = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.DELIVERED, paid=True, delivered_at=None)
        boundary = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.DELIVERED, paid=True, delivered_at=now - timedelta(hours=72))

        with patch("orders.models.timezone.now", return_value=now):
            self.assertTrue(online.can_request_refund)
            self.assertTrue(cod.can_request_refund)
            self.assertFalse(legacy.can_request_refund)
            self.assertTrue(boundary.can_request_refund)
            self.assert_action_visibility(online, cancel=False, refund=True)
            self.assertContains(self.client.get(reverse("orders:order_detail", args=[online.pk])), "Refund requests are available until")
            with override("fa"):
                response = self.client.get(reverse("orders:order_detail", args=[online.pk]))
            self.assertContains(response, "درخواست بازپرداخت تا")

        with patch("orders.models.timezone.now", return_value=now + timedelta(seconds=1)):
            self.assertFalse(boundary.can_request_refund)
            self.assertTrue(boundary.is_refund_window_closed)
            response = self.client.get(reverse("orders:order_detail", args=[boundary.pk]))
            self.assertContains(response, "The refund request period for this order has ended.")

    def test_ineligible_or_repeated_refund_service_calls_do_not_mutate(self):
        ineligible = self.order(status=Order.Status.SHIPPED, paid=False)
        with self.assertRaises(ValueError):
            create_refund_request(order=ineligible, actor=self.user, cleaned_data={"reason": Order.RefundReason.OTHER, "message": "No"})
        ineligible.refresh_from_db()
        self.assertEqual(ineligible.refund_status, Order.RefundStatus.NONE)

        eligible = self.order(method=Order.PaymentMethod.MOCK, paid=True)
        create_refund_request(order=eligible, actor=self.user, cleaned_data={"reason": Order.RefundReason.OTHER, "message": "First"})
        with self.assertRaises(ValueError):
            create_refund_request(order=eligible, actor=self.user, cleaned_data={"reason": Order.RefundReason.OTHER, "message": "Second"})
        eligible.refresh_from_db()
        self.assertEqual(eligible.refund_message, "First")

    def test_delivery_sets_timestamp_once_and_collects_cod_before_completion(self):
        online = self.order(method=Order.PaymentMethod.MOCK, status=Order.Status.SHIPPED, paid=True)
        self.assertTrue(mark_as_delivered(online, self.staff))
        online.refresh_from_db()
        online_delivered_at = online.delivered_at
        self.assertEqual(online.status, Order.Status.DELIVERED)
        self.assertIsNotNone(online_delivered_at)
        self.assertFalse(mark_as_delivered(online, self.staff))
        online.refresh_from_db()
        self.assertEqual(online.delivered_at, online_delivered_at)

        cod = self.order(status=Order.Status.SHIPPED)
        self.assertTrue(mark_as_delivered(cod, self.staff))
        cod.refresh_from_db()
        self.assertEqual(cod.status, Order.Status.DELIVERED)
        self.assertTrue(cod.is_paid)
        self.assertIsNotNone(cod.paid_at)
        self.assertIsNotNone(cod.delivered_at)

    def test_cod_collection_rejects_malformed_states_and_is_idempotent_when_paid(self):
        pending = self.order(status=Order.Status.PENDING)
        cancelled = self.order(status=Order.Status.CANCELLED)
        for order in (pending, cancelled):
            with self.assertRaises(ValueError):
                mark_cod_payment_collected(order, actor=self.staff)

        shipped = self.order(status=Order.Status.SHIPPED)
        mark_cod_payment_collected(shipped, actor=self.staff)
        shipped.refresh_from_db()
        paid_at = shipped.paid_at
        mark_cod_payment_collected(shipped, actor=self.staff)
        shipped.refresh_from_db()
        self.assertTrue(shipped.is_paid)
        self.assertEqual(shipped.paid_at, paid_at)
