from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Address
from orders.models import Order
from orders.services import cancel_unpaid_order
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


@override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=True)
class CheckoutIsolationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("checkout-user", password="secret")
        self.other_user = get_user_model().objects.create_user("other-user", password="secret")
        self.address = Address.objects.create(
            user=self.user,
            first_name="Checkout",
            last_name="User",
            phone="+989022813770",
            country="IR",
            province="Tehran",
            city="Tehran",
            street_address="1 Tile Street",
        )
        self.variant_a = self._variant("A", "Product A")
        self.variant_b = self._variant("B", "Product B")
        self.variant_c = self._variant("C", "Product C")
        self.client.force_login(self.user)

    def _variant(self, suffix, name):
        product = Product.objects.create(name=name, slug=f"product-{suffix.lower()}", sku=f"PRODUCT-{suffix}")
        variant = ProductVariant.objects.create(product=product, sku=f"VARIANT-{suffix}", stock=10)
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("100.00"))
        return variant

    def _create_online_order(self, variant):
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": variant.pk, "quantity": 1})
        self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""},
        )
        self.client.post(
            reverse("orders:checkout_payment"),
            {"payment_method": Order.PaymentMethod.MOCK, "coupon_code": ""},
        )
        self.client.get(reverse("orders:checkout_review"))
        token = self.client.session["checkout_attempt_token"]
        response = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})
        order = Order.objects.get(checkout_attempt_token=token)
        self.assertRedirects(
            response,
            reverse("orders:payment_start", args=[order.pk]),
            fetch_redirect_response=False,
        )
        return order, token

    def _simulate(self, order, outcome):
        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))
        self.assertRedirects(response, reverse("orders:mock_payment", args=[order.pk]))
        return self.client.post(
            reverse("orders:mock_payment", args=[order.pk]),
            {"outcome": outcome},
            follow=True,
        )

    def test_retryable_online_order_blocks_separate_checkout_and_preserves_new_cart(self):
        order_a, _ = self._create_online_order(self.variant_a)
        self._simulate(order_a, "failed")
        order_a.refresh_from_db()
        self.variant_a.refresh_from_db()

        self.assertFalse(order_a.is_paid)
        self.assertEqual(order_a.status, Order.Status.PENDING)
        self.assertEqual(self.client.session["cart"], {})
        self.assertEqual(self.variant_a.stock, 9)

        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant_b.pk, "quantity": 1})
        self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": "Morning"},
        )
        self.client.post(
            reverse("orders:checkout_payment"),
            {"payment_method": Order.PaymentMethod.MOCK, "coupon_code": ""},
        )
        self.client.get(reverse("orders:checkout_review"))
        token_b = self.client.session["checkout_attempt_token"]
        blocked = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token_b})

        self.assertEqual(blocked.status_code, 409)
        self.assertContains(blocked, "data-payment-conflict", status_code=409)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.client.session["cart"], {f"v_{self.variant_b.pk}": 1})
        self.assertEqual(self.client.session["checkout_shipping"]["delivery_slot"], "Morning")

        self._simulate(order_a, "success")
        response = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token_b})
        order_b = Order.objects.get(checkout_attempt_token=token_b)
        self.assertRedirects(response, reverse("orders:payment_start", args=[order_b.pk]), fetch_redirect_response=False)
        self._simulate(order_b, "success")
        order_a.refresh_from_db()
        order_b.refresh_from_db()

        self.assertNotEqual(order_a.pk, order_b.pk)
        self.assertEqual(order_a.items.get().variant_id, self.variant_a.pk)
        self.assertEqual(order_b.items.get().variant_id, self.variant_b.pk)
        self.assertTrue(order_a.is_paid)
        self.assertEqual(order_a.status, Order.Status.CONFIRMED)
        self.assertTrue(order_b.is_paid)
        self.assertEqual(order_b.status, Order.Status.CONFIRMED)

    def test_retrying_old_order_does_not_clear_a_newer_cart(self):
        order_a, _ = self._create_online_order(self.variant_a)
        self._simulate(order_a, "cancelled")
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant_c.pk, "quantity": 1})

        self._simulate(order_a, "success")
        order_a.refresh_from_db()

        self.assertTrue(order_a.is_paid)
        self.assertEqual(self.client.session["cart"], {f"v_{self.variant_c.pk}": 1})
        self.assertEqual(order_a.items.count(), 1)
        self.assertEqual(order_a.items.get().variant_id, self.variant_a.pk)

    def test_two_pending_orders_are_paid_independently(self):
        # Legacy/admin/imported rows remain independently payable; the
        # storefront guard only prevents creation of additional such rows.
        order_a = Order.objects.create(
            user=self.user, user_order_number=1, full_name="Buyer", address="1",
            city="Tehran", phone="123", payment_method=Order.PaymentMethod.MOCK,
            currency=Currency.USD, total_amount=Decimal("100.00"),
        )
        order_b = Order.objects.create(
            user=self.user, user_order_number=2, full_name="Buyer", address="1",
            city="Tehran", phone="123", payment_method=Order.PaymentMethod.MOCK,
            currency=Currency.USD, total_amount=Decimal("100.00"),
        )

        order_a.refresh_from_db()
        order_b.refresh_from_db()
        self.assertEqual(order_a.status, Order.Status.PENDING)
        self.assertEqual(order_b.status, Order.Status.PENDING)
        self.assertFalse(order_a.is_paid)
        self.assertFalse(order_b.is_paid)

        self._simulate(order_b, "success")
        order_a.refresh_from_db()
        order_b.refresh_from_db()
        self.assertFalse(order_a.is_paid)
        self.assertEqual(order_a.status, Order.Status.PENDING)
        self.assertTrue(order_b.is_paid)
        self.assertEqual(order_b.status, Order.Status.CONFIRMED)

        self._simulate(order_a, "success")
        order_a.refresh_from_db()
        self.assertTrue(order_a.is_paid)
        self.assertEqual(order_a.status, Order.Status.CONFIRMED)

    def test_paid_cancelled_and_cod_orders_do_not_block_new_online_checkout(self):
        for status, is_paid, payment_method in (
            (Order.Status.CONFIRMED, True, Order.PaymentMethod.MOCK),
            (Order.Status.CANCELLED, False, Order.PaymentMethod.MOCK),
            (Order.Status.CONFIRMED, False, Order.PaymentMethod.COD),
        ):
            with self.subTest(status=status, is_paid=is_paid, payment_method=payment_method):
                Order.objects.create(
                    user=self.user, full_name="Buyer", address="1", city="Tehran", phone="123",
                    status=status, is_paid=is_paid, payment_method=payment_method,
                    currency=Currency.USD,
                )
                self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant_a.pk, "quantity": 1})
                self.client.post(reverse("orders:checkout_shipping"), {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""})
                self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.MOCK})
                self.client.get(reverse("orders:checkout_review"))
                token = self.client.session["checkout_attempt_token"]
                response = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})
                order = Order.objects.get(checkout_attempt_token=token)
                self.assertRedirects(response, reverse("orders:payment_start", args=[order.pk]), fetch_redirect_response=False)
                self._simulate(order, "success")

    def test_legacy_multiple_retryable_orders_block_new_storefront_order_deterministically(self):
        first = Order.objects.create(
            user=self.user, user_order_number=1, full_name="Buyer", address="1", city="Tehran", phone="123",
            payment_method=Order.PaymentMethod.MOCK, currency=Currency.USD,
        )
        Order.objects.create(
            user=self.user, user_order_number=2, full_name="Buyer", address="1", city="Tehran", phone="123",
            payment_method=Order.PaymentMethod.MOCK, currency=Currency.USD,
        )
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant_a.pk, "quantity": 1})
        self.client.post(reverse("orders:checkout_shipping"), {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""})
        self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.MOCK})
        self.client.get(reverse("orders:checkout_review"))
        token = self.client.session["checkout_attempt_token"]
        response = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, reverse("orders:payment_start", args=[first.pk]), status_code=409)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 2)

    def test_same_checkout_token_replay_creates_exactly_one_order(self):
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant_a.pk, "quantity": 1})
        self.client.post(reverse("orders:checkout_shipping"), {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""})
        self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.MOCK})
        self.client.get(reverse("orders:checkout_review"))
        token = self.client.session["checkout_attempt_token"]

        first = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})
        order = Order.objects.get(checkout_attempt_token=token)
        replay = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})

        self.assertRedirects(first, reverse("orders:payment_start", args=[order.pk]), fetch_redirect_response=False)
        self.assertRedirects(replay, reverse("orders:payment_start", args=[order.pk]), fetch_redirect_response=False)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)

    def test_cancelled_order_and_late_callback_cannot_be_paid(self):
        order, _ = self._create_online_order(self.variant_a)
        cancel_unpaid_order(order=order, actor=self.user)

        response = self.client.get(reverse("orders:payment_callback", args=[order.pk]) + "?result=success")
        order.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertFalse(order.is_paid)

    def test_mock_failure_and_cancellation_keep_order_retryable(self):
        for outcome in ("failed", "cancelled"):
            with self.subTest(outcome=outcome):
                order, _ = self._create_online_order(self.variant_a if outcome == "failed" else self.variant_b)
                self._simulate(order, outcome)
                order.refresh_from_db()
                self.assertEqual(order.status, Order.Status.PENDING)
                self.assertFalse(order.is_paid)
                self.assertTrue(order.can_start_online_payment)
                # Each subtest must leave the one-order storefront guard clear.
                cancel_unpaid_order(order=order, actor=self.user)

    def test_payment_start_rejects_invalid_states_and_other_users(self):
        invalid_statuses = [Order.Status.CONFIRMED, Order.Status.ON_HOLD, Order.Status.SHIPPED, Order.Status.DELIVERED, Order.Status.CANCELLED]
        for status in invalid_statuses:
            order = Order.objects.create(
                user=self.user,
                full_name="Buyer",
                address="1",
                city="Tehran",
                phone="123",
                status=status,
                payment_method=Order.PaymentMethod.MOCK,
                currency=Currency.USD,
            )
            response = self.client.get(reverse("orders:payment_start", args=[order.pk]))
            self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))

        foreign_order = Order.objects.create(
            user=self.other_user,
            full_name="Other",
            address="1",
            city="Tehran",
            phone="123",
            payment_method=Order.PaymentMethod.MOCK,
            currency=Currency.USD,
        )
        response = self.client.get(reverse("orders:payment_start", args=[foreign_order.pk]))
        self.assertEqual(response.status_code, 404)
