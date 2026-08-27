from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.translation import override

from accounts.models import Address
from orders.models import Order
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


@override_settings(MOCK_PAYMENT_SIMULATION_ENABLED=False)
class CommerceReadinessBlockerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("commerce-blocker", password="secret")
        self.address = Address.objects.create(
            user=self.user, first_name="Commerce", last_name="Buyer", phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 Test Way",
        )
        product = Product.objects.create(name="Generic adhesive", slug="generic-adhesive", sku="GEN-ADH")
        self.variant = ProductVariant.objects.create(
            product=product, sku="GEN-ADH-BAG", stock=4, price_unit=ProductVariant.PriceUnit.BAG,
        )
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("20.00"))
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("200000.00"))
        self.client.force_login(self.user)

    def _prepare_cod_checkout(self, *, delivery_slot="Morning"):
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
        response = self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": delivery_slot},
        )
        self.assertRedirects(response, reverse("orders:checkout_payment"), fetch_redirect_response=False)
        response = self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.COD})
        self.assertRedirects(response, reverse("orders:checkout_review"), fetch_redirect_response=False)
        self.client.get(reverse("orders:checkout_review"))
        return self.client.session["checkout_attempt_token"]

    def test_disabled_mock_is_absent_and_crafted_paths_cannot_pay(self):
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
        self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""},
        )
        payment_page = self.client.get(reverse("orders:checkout_payment"))
        self.assertNotContains(payment_page, 'data-payment-method="mock"')
        self.assertContains(payment_page, 'data-payment-method="cod"')

        crafted = self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.MOCK})
        self.assertEqual(crafted.status_code, 200)
        self.assertNotIn("checkout_payment", self.client.session)

        order = Order.objects.create(
            user=self.user, full_name="Commerce Buyer", address="1 Test Way", city="Tehran", phone="123",
            payment_method=Order.PaymentMethod.MOCK, currency=Currency.USD, total_amount=Decimal("20.00"),
        )
        start = self.client.get(reverse("orders:payment_start", args=[order.pk]))
        self.assertRedirects(start, reverse("orders:order_detail", args=[order.pk]), fetch_redirect_response=False)
        callback = self.client.get(reverse("orders:payment_callback", args=[order.pk]) + "?result=success")
        self.assertEqual(callback.status_code, 200)
        order.refresh_from_db()
        self.assertFalse(order.is_paid)
        self.assertEqual(order.status, Order.Status.PENDING)

    def test_delivery_slot_is_validated_reviewed_and_persisted_for_cod(self):
        token = self._prepare_cod_checkout(delivery_slot="Morning")
        self.assertEqual(self.client.session["checkout_shipping"]["delivery_slot"], "Morning")
        review = self.client.get(reverse("orders:checkout_review"))
        self.assertContains(review, "Morning")
        response = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})
        order = Order.objects.get(checkout_attempt_token=token)
        self.assertRedirects(response, reverse("orders:order_result", args=[order.pk]), fetch_redirect_response=False)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(order.delivery_slot, "Morning")
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertFalse(order.is_paid)
        self.assertEqual(self.variant.stock, 3)
        self.assertEqual(self.client.session["cart"], {})

    def test_delivery_slot_rejects_tampering_and_order_note_is_not_exposed(self):
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
        invalid = self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": "x" * 101},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, 'id="delivery-slot-errors"')
        self.assertNotContains(invalid, 'name="order_note"')
        with override("fa"):
            self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
            persian = self.client.get(reverse("orders:checkout_shipping"), follow=True)
        self.assertContains(persian, 'name="delivery_slot"')
