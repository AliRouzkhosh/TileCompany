from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Address
from orders.models import Order
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


class StaleCheckoutQuantityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("stale-quantity", password="secret")
        self.address = Address.objects.create(
            user=self.user, first_name="Stale", last_name="Buyer", phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 Test Way",
        )
        self.variant_a = self._variant("A", stock=3)
        self.variant_b = self._variant("B", stock=2)
        self.variant_c = self._variant("C", stock=3)
        self.client.force_login(self.user)

    def _variant(self, suffix, *, stock):
        product = Product.objects.create(name=f"Generic product {suffix}", slug=f"generic-product-{suffix.lower()}", sku=f"GEN-{suffix}")
        variant = ProductVariant.objects.create(
            product=product, sku=f"GEN-{suffix}-BAG", stock=stock,
            price_unit=ProductVariant.PriceUnit.BAG,
        )
        VariantPrice.objects.create(variant=variant, currency=Currency.TOMAN, amount=Decimal("100000.00"))
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("10.00"))
        return variant

    def _reach_review(self, lines):
        for variant, quantity in lines:
            self.client.post(reverse("orders:cart_add_variant"), {"variant_id": variant.pk, "quantity": quantity})
        self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""},
        )
        self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.COD})
        self.client.get(reverse("orders:checkout_review"))
        return self.client.session["checkout_attempt_token"]

    def _place_order(self, token):
        return self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})

    def test_stale_review_quantity_is_rejected_without_cart_mutation(self):
        token = self._reach_review([(self.variant_b, 2)])
        self.variant_b.stock = 1
        self.variant_b.save(update_fields=["stock"])

        response = self._place_order(token)

        self.assertRedirects(response, reverse("orders:checkout_shipping"), fetch_redirect_response=False)
        self.assertEqual(Order.objects.count(), 0)
        self.variant_b.refresh_from_db()
        self.assertEqual(self.variant_b.stock, 1)
        self.assertEqual(self.client.session["cart"], {f"v_{self.variant_b.pk}": 2})
        self.assertEqual(self.client.session["checkout_attempt_token"], token)
        self.assertContains(self.client.get(reverse("orders:cart_detail")), "Requested 2; only 1")

    def test_mixed_cart_stale_line_rolls_back_every_line(self):
        token = self._reach_review([(self.variant_a, 1), (self.variant_b, 2), (self.variant_c, 1)])
        self.variant_b.stock = 1
        self.variant_b.save(update_fields=["stock"])

        self._place_order(token)

        self.assertEqual(Order.objects.count(), 0)
        for variant, expected in ((self.variant_a, 3), (self.variant_b, 1), (self.variant_c, 3)):
            variant.refresh_from_db()
            self.assertEqual(variant.stock, expected)

    def test_exact_stock_and_lower_quantity_succeed_with_the_requested_quantity(self):
        self.variant_a.stock = 1
        self.variant_a.save(update_fields=["stock"])
        token = self._reach_review([(self.variant_a, 1)])
        self._place_order(token)
        order = Order.objects.get(checkout_attempt_token=token)
        self.assertEqual(order.items.get().quantity, 1)
        self.variant_a.refresh_from_db()
        self.assertEqual(self.variant_a.stock, 0)

    def test_customer_can_explicitly_correct_quantity_after_rejection(self):
        token = self._reach_review([(self.variant_b, 2)])
        self.variant_b.stock = 1
        self.variant_b.save(update_fields=["stock"])
        self._place_order(token)

        self.client.post(reverse("orders:cart_update", args=[f"v_{self.variant_b.pk}"]), {"quantity": 1})
        replacement_token = self._reach_review([])
        self._place_order(replacement_token)

        order = Order.objects.get(checkout_attempt_token=replacement_token)
        self.assertEqual(order.items.get().quantity, 1)
        self.variant_b.refresh_from_db()
        self.assertEqual(self.variant_b.stock, 0)
