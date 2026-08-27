from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import gettext, override

from orders.models import Order, OrderItem, OrderNote, OrderStatusHistory
from orders.payment import BasePaymentGateway, DevelopmentZarinPalGateway, PaymentResult
from orders.context_processors import payment_attention
from notifications.models import Notification, NotificationEvent
from notifications.services import reverse_notification_target
from products.currency import Currency
from products.models import Product, ProductVariant


class _VerifiedGateway(BasePaymentGateway):
    provider = "Test provider"

    def __init__(self):
        self.requested = None
        self.verified = None

    def request(self, amount, description, callback_url, mobile="", email=""):
        self.requested = (amount, description, callback_url, mobile)
        return "AUTH-EXACT"

    def payment_url(self, authority):
        return "https://provider.invalid/pay/" + authority

    def verify(self, amount, authority):
        self.verified = (amount, authority)
        return PaymentResult(
            success=True,
            state="success",
            payment_reference="REF-VERIFIED",
            provider=self.provider,
            authority=authority,
        )


class StorefrontV5DResultTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("v5d-buyer", password="secret")
        self.other = get_user_model().objects.create_user("v5d-other", password="secret")
        self.client.force_login(self.user)

    def order(self, *, method=Order.PaymentMethod.MOCK, status=Order.Status.PENDING,
              paid=False, currency=Currency.USD, reference=""):
        return Order.objects.create(
            user=self.user,
            user_order_number=7,
            full_name="V5D Buyer",
            address="1 Commerce Street",
            city="Tehran",
            phone="123",
            payment_method=method,
            status=status,
            is_paid=paid,
            currency=currency,
            total_amount=Decimal("123.45") if currency == Currency.USD else Decimal("6079000"),
            payment_reference=reference,
            payment_provider="Verified provider" if paid else "",
        )

    def test_cod_confirmation_is_confirmed_but_truthfully_unpaid(self):
        order = self.order(method=Order.PaymentMethod.COD, status=Order.Status.CONFIRMED)

        response = self.client.get(reverse("orders:order_result", args=[order.pk]))

        self.assertContains(response, 'data-result-state="cod"')
        self.assertContains(response, "Order confirmed")
        self.assertContains(response, "Payment due on delivery")
        self.assertContains(response, "#7")
        self.assertNotContains(response, "Payment successful")
        self.assertFalse(order.is_paid)

    def test_persisted_order_state_controls_success_pending_and_cancelled(self):
        paid = self.order(status=Order.Status.CONFIRMED, paid=True, reference="SAFE-REF")
        pending = self.order()
        cancelled = self.order(status=Order.Status.CANCELLED)

        paid_response = self.client.get(reverse("orders:order_result", args=[paid.pk]))
        pending_response = self.client.get(reverse("orders:order_result", args=[pending.pk]))
        cancelled_response = self.client.get(reverse("orders:order_result", args=[cancelled.pk]))

        self.assertContains(paid_response, 'data-result-state="success"')
        self.assertContains(paid_response, "SAFE-REF")
        self.assertContains(pending_response, 'data-result-state="pending"')
        self.assertContains(pending_response, "Retry payment")
        self.assertContains(cancelled_response, 'data-result-state="order_cancelled"')
        self.assertNotContains(cancelled_response, "Retry payment")

    @override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=True)
    def test_mock_failure_and_customer_cancel_are_distinct_and_retryable(self):
        for outcome, state, heading in (
            ("failed", "failure", "Payment failed"),
            ("cancelled", "cancelled", "Payment cancelled."),
        ):
            with self.subTest(outcome=outcome):
                order = self.order(reference="MOCK_AUTHORITY")
                response = self.client.get(
                    reverse("orders:payment_callback", args=[order.pk]) + f"?result={outcome}"
                )
                order.refresh_from_db()
                self.assertContains(response, f'data-result-state="{state}"')
                self.assertContains(response, heading)
                self.assertContains(response, "Retry payment")
                self.assertFalse(order.is_paid)
                self.assertEqual(order.status, Order.Status.PENDING)

    @override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=True)
    def test_verified_success_and_callback_refresh_mutate_payment_once(self):
        order = self.order(reference="MOCK_AUTHORITY")
        callback = reverse("orders:payment_callback", args=[order.pk]) + "?result=success"

        first = self.client.get(callback)
        note_count = OrderNote.objects.filter(order=order).count()
        history_count = OrderStatusHistory.objects.filter(order=order).count()
        second = self.client.get(callback)
        order.refresh_from_db()

        self.assertContains(first, 'data-result-state="success"')
        self.assertContains(second, 'data-result-state="success"')
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(OrderNote.objects.filter(order=order).count(), note_count)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order).count(), history_count)

    def test_result_route_requires_authentication_and_exact_ownership(self):
        own = self.order()
        foreign = Order.objects.create(
            user=self.other, full_name="Other", address="2", city="Tehran", phone="456"
        )
        self.client.logout()
        response = self.client.get(reverse("orders:order_result", args=[own.pk]))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(reverse("orders:order_result", args=[foreign.pk]))
        self.assertEqual(response.status_code, 404)

    @override_settings(ZARINPAL_MERCHANT_ID="test-merchant", ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=False)
    def test_toman_gateway_uses_exact_amount_authority_and_verified_result(self):
        order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)
        gateway = _VerifiedGateway()
        with patch("orders.payment.get_payment_gateway_for_order", return_value=gateway):
            start = self.client.get(reverse("orders:payment_start", args=[order.pk]))
            self.assertEqual(start.status_code, 302)
            self.assertEqual(start.url, "https://provider.invalid/pay/AUTH-EXACT")
            self.assertEqual(gateway.requested[0], Decimal("6079000"))
            order.refresh_from_db()
            self.assertEqual(order.payment_reference, "AUTH-EXACT")

            result = self.client.get(
                reverse("orders:payment_callback", args=[order.pk])
                + "?Status=OK&Authority=AUTH-EXACT"
            )

        order.refresh_from_db()
        self.assertEqual(gateway.verified, (Decimal("6079000"), "AUTH-EXACT"))
        self.assertContains(result, 'data-result-state="success"')
        self.assertTrue(order.is_paid)
        self.assertEqual(order.payment_reference, "REF-VERIFIED")

    def _start_development_zarinpal(self, order):
        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))
        self.assertRedirects(response, reverse("orders:mock_payment", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.payment_reference, "DEV_ZARINPAL_AUTHORITY")

    @override_settings(
        ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=True,
        MOCK_PAYMENT_SIMULATION_ENABLED=True,
    )
    def test_development_zarinpal_simulator_uses_existing_callback_verification_success(self):
        order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)
        self._start_development_zarinpal(order)

        original_verify = DevelopmentZarinPalGateway.verify
        with patch.object(
            DevelopmentZarinPalGateway,
            "verify",
            autospec=True,
            side_effect=original_verify,
        ) as verify:
            response = self.client.post(
                reverse("orders:mock_payment", args=[order.pk]),
                {"outcome": "success"},
                follow=True,
            )

        order.refresh_from_db()
        self.assertContains(response, 'data-result-state="success"')
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.payment_reference, "DEV-ZARINPAL-REF-100001")
        self.assertEqual(verify.call_args.args[1], Decimal("6079000"))
        self.assertEqual(verify.call_args.args[2], "DEV_ZARINPAL_AUTHORITY")
        self.assertEqual(verify.call_args.kwargs["result"], "success")

    @override_settings(
        ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=True,
        MOCK_PAYMENT_SIMULATION_ENABLED=True,
    )
    def test_development_zarinpal_failure_and_cancel_are_unpaid_and_retryable(self):
        for outcome, state in (("failed", "failure"), ("cancelled", "cancelled")):
            with self.subTest(outcome=outcome):
                order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)
                self._start_development_zarinpal(order)

                response = self.client.post(
                    reverse("orders:mock_payment", args=[order.pk]),
                    {"outcome": outcome},
                    follow=True,
                )

                order.refresh_from_db()
                self.assertContains(response, f'data-result-state="{state}"')
                self.assertFalse(order.is_paid)
                self.assertEqual(order.status, Order.Status.PENDING)
                self.assertTrue(order.can_start_online_payment)

    @override_settings(
        ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=True,
        MOCK_PAYMENT_SIMULATION_ENABLED=True,
    )
    def test_development_zarinpal_repeat_callback_mutates_payment_once(self):
        order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)
        product = Product.objects.create(name="Toman stock", slug="toman-stock", sku="TOMAN-STOCK")
        variant = ProductVariant.objects.create(product=product, sku="TOMAN-STOCK-1", stock=7)
        OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            product_name=product.name,
            unit_price=Decimal("6079000"),
            quantity=1,
        )
        self._start_development_zarinpal(order)
        callback = reverse("orders:payment_callback", args=[order.pk]) + "?result=success"

        self.client.get(callback)
        note_count = OrderNote.objects.filter(order=order).count()
        history_count = OrderStatusHistory.objects.filter(order=order).count()
        response = self.client.get(callback)
        order.refresh_from_db()

        self.assertContains(response, 'data-result-state="success"')
        self.assertTrue(order.is_paid)
        self.assertEqual(OrderNote.objects.filter(order=order).count(), note_count)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order).count(), history_count)
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 7)

    @override_settings(
        ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=False,
        MOCK_PAYMENT_SIMULATION_ENABLED=True,
        ZARINPAL_MERCHANT_ID="test-merchant",
    )
    def test_zarinpal_simulator_is_not_available_when_development_gateway_is_disabled(self):
        order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)

        response = self.client.get(reverse("orders:mock_payment", args=[order.pk]))

        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))

    @override_settings(
        ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=True,
        MOCK_PAYMENT_SIMULATION_ENABLED=False,
    )
    def test_development_zarinpal_never_auto_succeeds_when_simulation_is_disabled(self):
        order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)

        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))

        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertFalse(order.is_paid)
        self.assertEqual(order.payment_reference, "DEV_ZARINPAL_AUTHORITY")

    @override_settings(
        ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=True,
        MOCK_PAYMENT_SIMULATION_ENABLED=True,
    )
    def test_development_zarinpal_simulator_keeps_ownership_protection(self):
        order = self.order(method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN)
        self.client.force_login(self.other)

        response = self.client.get(reverse("orders:mock_payment", args=[order.pk]))

        self.assertEqual(response.status_code, 404)

    def test_result_page_query_count_does_not_grow_with_order_items(self):
        order = self.order(status=Order.Status.CONFIRMED, paid=True)
        product = Product.objects.create(name="Generic device", slug="generic-device", sku="GENERIC")
        variant = ProductVariant.objects.create(product=product, sku="GENERIC-8-256", stock=20)
        for index in range(8):
            OrderItem.objects.create(
                order=order,
                product=product,
                variant=variant,
                product_name=f"Generic device {index}",
                unit_price=Decimal("10"),
                quantity=1,
                variant_snapshot={"version": 2, "configuration": [{"label": "Memory", "values": ["8 GB"]}]},
            )
        url = reverse("orders:order_result", args=[order.pk])
        self.client.get(url)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # The shared Dashboard-link eligibility contract adds the standard
        # direct/Group permission lookups, not per-order-item work.
        self.assertLessEqual(len(queries), 10)

    def test_result_templates_are_one_semantic_surface_without_business_hardcoding(self):
        root = Path(__file__).resolve().parent / "templates" / "orders"
        source = (root / "payment_result.html").read_text(encoding="utf-8")
        source += (root / "components" / "payment_result_card.html").read_text(encoding="utf-8")
        lowered = source.lower()
        self.assertEqual(source.count("<h1"), 1)
        self.assertEqual(source.count("data-result-state"), 1)
        self.assertNotIn("checkout_steps", source)
        self.assertNotIn("<form", lowered)
        for raw_palette in ("stone-", "amber-", "green-", "red-"):
            self.assertNotIn(raw_palette, lowered)
        for product_branch in ("tile", "material", "finish", "color", "ram", "storage"):
            self.assertNotIn(product_branch, lowered)

    def test_farsi_result_uses_localized_authoritative_copy(self):
        order = self.order(method=Order.PaymentMethod.COD, status=Order.Status.CONFIRMED,
                           currency=Currency.TOMAN)
        with override("fa"):
            response = self.client.get(reverse("orders:order_result", args=[order.pk]))
        self.assertContains(response, "سفارش تأیید شد")
        self.assertNotContains(response, "Order confirmed")


class StorefrontV5DPaymentRecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("recovery-buyer", password="secret")
        self.other = get_user_model().objects.create_user("recovery-other", password="secret")
        self.client.force_login(self.user)
        self.factory = RequestFactory()

    def order(self, *, method=Order.PaymentMethod.MOCK, status=Order.Status.PENDING, paid=False, user=None):
        return Order.objects.create(
            user=user or self.user,
            user_order_number=Order.objects.filter(user=user or self.user).count() + 1,
            full_name="Recovery Buyer",
            address="1 Payment Street",
            city="Tehran",
            phone="123",
            payment_method=method,
            status=status,
            is_paid=paid,
            currency=Currency.USD,
            total_amount=Decimal("123.45"),
        )

    @override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=True)
    def test_failure_and_cancellation_create_distinct_deduplicated_recovery_notifications(self):
        for outcome, event_type, title in (
            ("failed", NotificationEvent.PAYMENT_FAILED, "Payment failed"),
            ("cancelled", NotificationEvent.PAYMENT_CANCELLED, "Payment cancelled"),
        ):
            with self.subTest(outcome=outcome):
                order = self.order()
                callback = reverse("orders:payment_callback", args=[order.pk]) + f"?result={outcome}"
                response = self.client.get(callback)
                self.client.get(callback)
                self.client.get(reverse("orders:order_result", args=[order.pk]))

                notification = Notification.objects.get(user=self.user, event_type=event_type, payload__order_id=order.id)
                self.assertContains(response, title)
                self.assertEqual(notification.target_url, reverse_notification_target("orders:order_detail", kwargs={"order_id": order.id}))
                self.assertEqual(Notification.objects.filter(user=self.user, event_type=event_type, payload__order_id=order.id).count(), 1)

    @override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=True)
    def test_success_and_cod_never_create_payment_recovery_notifications(self):
        online = self.order()
        self.client.get(reverse("orders:payment_callback", args=[online.pk]) + "?result=success")
        cod = self.order(method=Order.PaymentMethod.COD, status=Order.Status.CONFIRMED)
        self.client.get(reverse("orders:order_result", args=[cod.pk]))

        self.assertFalse(Notification.objects.filter(event_type__in=[NotificationEvent.PAYMENT_FAILED, NotificationEvent.PAYMENT_CANCELLED]).exists())

    @override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=True)
    def test_notification_action_opens_order_detail_and_preserves_current_state_safety(self):
        order = self.order()
        self.client.get(reverse("orders:payment_callback", args=[order.pk]) + "?result=failed")
        notification = Notification.objects.get(event_type=NotificationEvent.PAYMENT_FAILED)

        opened = self.client.post(reverse("notifications:open", args=[notification.pk]))
        self.assertRedirects(opened, notification.target_url, fetch_redirect_response=False)

        self.client.get(reverse("orders:payment_callback", args=[order.pk]) + "?result=success")
        detail = self.client.get(notification.target_url)
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "data-order-payment-recovery")

    def test_payment_attention_is_auth_aware_and_uses_authoritative_online_retry_state(self):
        home = reverse("products:home")
        anonymous = self.client.logout() or self.client.get(home)
        self.assertNotContains(anonymous, "data-payment-attention")
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(home), "data-payment-attention")

        one = self.order()
        response = self.client.get(home)
        self.assertContains(response, "data-payment-attention")
        self.assertContains(response, "Order #1 is awaiting payment.")
        self.assertContains(response, "Review and pay")
        self.assertEqual(response.context["payment_attention"]["action_url"], reverse("orders:order_detail", args=[one.pk]))

        self.order()
        multiple = self.client.get(home)
        self.assertContains(multiple, "2 orders are awaiting payment.")
        self.assertContains(multiple, "View orders")
        self.assertEqual(multiple.context["payment_attention"]["action_url"], reverse("orders:order_list"))

        one.is_paid = True
        one.status = Order.Status.CONFIRMED
        one.save(update_fields=["is_paid", "status"])
        other = Order.objects.filter(user=self.user, is_paid=False).first()
        other.status = Order.Status.CANCELLED
        other.save(update_fields=["status"])
        self.assertNotContains(self.client.get(home), "data-payment-attention")

    def test_payment_attention_excludes_cod_and_uses_one_order_query_without_items(self):
        self.order(method=Order.PaymentMethod.COD, status=Order.Status.CONFIRMED)
        request = self.factory.get("/")
        request.user = self.user
        with CaptureQueriesContext(connection) as queries:
            context = payment_attention(request)
        self.assertEqual(context, {"payment_attention": None})
        self.assertEqual(len(queries), 1)
        self.assertNotIn("orders_orderitem", queries[0]["sql"].lower())

        request.user = AnonymousUser()
        with CaptureQueriesContext(connection) as queries:
            anonymous_context = payment_attention(request)
        self.assertEqual(anonymous_context, {"payment_attention": None})
        self.assertEqual(len(queries), 0)

    def test_order_detail_payment_recovery_is_guarded_and_does_not_mutate(self):
        retryable = self.order()
        response = self.client.get(reverse("orders:order_detail", args=[retryable.pk]))
        retryable.refresh_from_db()
        self.assertContains(response, "data-order-payment-recovery")
        self.assertContains(response, "Complete payment")
        self.assertContains(response, reverse("orders:payment_start", args=[retryable.pk]))
        self.assertFalse(retryable.is_paid)
        self.assertEqual(retryable.status, Order.Status.PENDING)

        for order in (
            self.order(status=Order.Status.CONFIRMED, paid=True),
            self.order(method=Order.PaymentMethod.COD, status=Order.Status.CONFIRMED),
            self.order(status=Order.Status.CANCELLED),
        ):
            with self.subTest(order=order.pk):
                response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
                self.assertNotContains(response, "data-order-payment-recovery")
                self.assertNotContains(response, "Complete payment")

        other_order = self.order(user=self.other)
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[other_order.pk])).status_code, 404)

    def test_payment_recovery_labels_are_localized(self):
        with override("fa"):
            self.assertEqual(gettext("Complete payment"), "تکمیل پرداخت")
            self.assertEqual(gettext("Review and pay"), "مشاهده و پرداخت")
