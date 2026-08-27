from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Address
from .forms import PaymentMethodForm
from .models import Order, OrderEmailDelivery, OrderItem
from .services import confirm_cod_order, mark_cod_payment_collected, mark_order_paid


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://example.test")
class TransactionalOrderEmailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="buyer", email="old@example.com", password="secret-pass-123"
        )

    def order(self, *, method=Order.PaymentMethod.MOCK, requested=True, language="en"):
        order = Order.objects.create(
            user=self.user,
            user_order_number=44,
            full_name="Historic Buyer",
            address="1 Historic Street",
            city="Tehran",
            phone="+989121234567",
            payment_method=method,
            total_amount=Decimal("125.00"),
            shipping_cost=Decimal("5.00"),
            email_receipt_requested=requested,
            receipt_email="old@example.com" if requested else "",
            communication_language=language,
        )
        OrderItem.objects.create(
            order=order,
            product_name="Historic Tile (SKU-OLD)",
            unit_price=Decimal("60.00"),
            quantity=2,
            variant_snapshot={"sku": "SKU-OLD"},
        )
        return order

    def commit_callbacks(self):
        return self.captureOnCommitCallbacks(execute=True)

    def test_requested_online_payment_creates_one_historical_paid_receipt(self):
        order = self.order()
        with self.commit_callbacks():
            mark_order_paid(order, "provider-reference", "Mock")
        delivery = OrderEmailDelivery.objects.get(order=order)
        self.assertEqual(delivery.kind, OrderEmailDelivery.Kind.ONLINE_PAID_RECEIPT)
        self.assertEqual(delivery.status, OrderEmailDelivery.Status.SENT)
        self.assertEqual(delivery.recipient, "old@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["old@example.com"])
        self.assertIn("Historic Tile (SKU-OLD)", mail.outbox[0].body)
        self.assertNotIn("provider-reference", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)

    def test_unrequested_or_failed_online_payment_sends_nothing(self):
        unrequested = self.order(requested=False)
        with self.commit_callbacks():
            mark_order_paid(unrequested, "reference", "Mock")
        self.assertFalse(OrderEmailDelivery.objects.filter(order=unrequested).exists())
        self.assertEqual(len(mail.outbox), 0)

        failed = self.order()
        self.assertFalse(failed.is_paid)
        self.assertFalse(OrderEmailDelivery.objects.filter(order=failed).exists())

    def test_duplicate_online_callback_and_changed_user_email_do_not_duplicate_or_change_recipient(self):
        order = self.order()
        with self.commit_callbacks():
            mark_order_paid(order, "ref", "Mock")
        self.user.email = "new@example.com"
        self.user.save(update_fields=["email"])
        with self.commit_callbacks():
            mark_order_paid(order, "ref", "Mock")
        self.assertEqual(OrderEmailDelivery.objects.filter(order=order).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["old@example.com"])

    def test_cod_confirmation_and_paid_receipt_are_distinct_and_idempotent(self):
        order = self.order(method=Order.PaymentMethod.COD)
        with self.commit_callbacks():
            confirm_cod_order(order, actor=self.user)
        with self.commit_callbacks():
            confirm_cod_order(order, actor=self.user)
        self.assertEqual(OrderEmailDelivery.objects.filter(order=order).count(), 1)
        self.assertEqual(mail.outbox[0].subject, "Your Cash on Delivery order confirmation")
        self.assertIn("Payment is due on delivery", mail.outbox[0].body)

        order.refresh_from_db()
        order.status = Order.Status.SHIPPED
        order.save(update_fields=["status"])
        with self.commit_callbacks():
            mark_cod_payment_collected(order, actor=self.user)
        with self.commit_callbacks():
            mark_cod_payment_collected(order, actor=self.user)
        self.assertEqual(OrderEmailDelivery.objects.filter(order=order).count(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[1].subject, "Your Cash on Delivery payment receipt")

    def test_delivery_failure_does_not_roll_back_paid_order_and_is_retryable(self):
        order = self.order()
        with patch("orders.email_delivery.send_transactional_email", side_effect=OSError("smtp unavailable")):
            with self.commit_callbacks():
                mark_order_paid(order, "ref", "Mock")
        order.refresh_from_db()
        delivery = OrderEmailDelivery.objects.get(order=order)
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(delivery.status, OrderEmailDelivery.Status.FAILED)
        self.assertEqual(delivery.failure_code, "OSError")

    def test_farsi_order_email_uses_order_locale(self):
        order = self.order(language="fa")
        with self.commit_callbacks():
            mark_order_paid(order, "ref", "Mock")
        self.assertIn("رسید سفارش پرداخت‌شده", mail.outbox[0].subject)
        self.assertIn("dir=\"rtl\"", mail.outbox[0].alternatives[0].content)


class CheckoutReceiptPreferenceTests(TestCase):
    def test_receipt_request_requires_valid_account_email(self):
        user = get_user_model().objects.create_user("no-email", password="secret")
        form = PaymentMethodForm(
            {"payment_method": "cod", "email_receipt_requested": "on"},
            payment_choices=[("cod", "COD")],
            customer=user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email_receipt_requested", form.errors)

    def test_receipt_request_snapshots_valid_account_email(self):
        user = get_user_model().objects.create_user("email", email="buyer@example.com", password="secret")
        form = PaymentMethodForm(
            {"payment_method": "cod", "email_receipt_requested": "on"},
            payment_choices=[("cod", "COD")],
            customer=user,
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["receipt_email"], "buyer@example.com")
