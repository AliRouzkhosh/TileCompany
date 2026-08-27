from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Address
from orders.models import Order, OrderItem, OrderStatusHistory
from orders.services import get_payment_choices
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


@override_settings(LOCAL_PAYMENT_SIMULATOR_ENABLED=True, MOCK_PAYMENT_SIMULATION_ENABLED=False)
class LocalPaymentSimulatorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("simulator-owner", password="secret")
        self.other = get_user_model().objects.create_user("simulator-other", password="secret")
        self.address = Address.objects.create(user=self.user, first_name="Local", last_name="Buyer", phone="123", country="IR", province="Tehran", city="Tehran", street_address="1 Test Way")
        self.product = Product.objects.create(name="Simulator adhesive", slug="simulator-adhesive", sku="SIM-ADH")
        self.variant = ProductVariant.objects.create(product=self.product, sku="SIM-ADH-BAG", stock=5, price_unit=ProductVariant.PriceUnit.BAG)
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("20.00"))
        self.client.force_login(self.user)

    def order(self):
        order = Order.objects.create(user=self.user, user_order_number=1, shipping_address=self.address, full_name="Local Buyer", address="1 Test Way", city="Tehran", phone="123", payment_method=Order.PaymentMethod.MOCK, currency=Currency.USD, total_amount=Decimal("20.00"))
        OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, unit_price=Decimal("20.00"), quantity=1, variant_snapshot={"schema_version": 2})
        OrderStatusHistory.objects.create(order=order, status=Order.Status.PENDING, note="Order created")
        return order

    def start(self, order):
        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))
        self.assertRedirects(response, reverse("orders:local_payment_simulator", args=[order.pk]), fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertTrue(order.payment_reference.startswith("LOCAL-"))
        return order

    def test_success_uses_callback_and_is_idempotent(self):
        order = self.start(self.order())
        action = self.client.post(reverse("orders:local_payment_simulator", args=[order.pk]), {"outcome": "success"})
        first = self.client.get(action.url)
        histories = OrderStatusHistory.objects.filter(order=order).count()
        second = self.client.get(action.url)
        order.refresh_from_db()
        self.assertContains(first, 'data-result-state="success"')
        self.assertContains(second, 'data-result-state="success"')
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order).count(), histories)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 5)

    def test_checkout_offers_simulator_and_completes_through_callback(self):
        self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
        self.client.post(reverse("orders:checkout_shipping"), {"address_id": self.address.pk, "shipping_method": "standard", "delivery_slot": ""})
        payment_page = self.client.get(reverse("orders:checkout_payment"))
        self.assertContains(payment_page, "Simulated online payment")
        self.client.post(reverse("orders:checkout_payment"), {"payment_method": Order.PaymentMethod.MOCK})
        self.client.get(reverse("orders:checkout_review"))
        token = self.client.session["checkout_attempt_token"]
        created = self.client.post(reverse("orders:checkout_review"), {"checkout_attempt_token": token})
        order = Order.objects.get(checkout_attempt_token=token)
        self.assertRedirects(created, reverse("orders:payment_start", args=[order.pk]), fetch_redirect_response=False)
        initiated = self.client.get(created.url)
        self.assertRedirects(initiated, reverse("orders:local_payment_simulator", args=[order.pk]), fetch_redirect_response=False)
        action = self.client.post(reverse("orders:local_payment_simulator", args=[order.pk]), {"outcome": "success"})
        self.client.get(action.url)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertEqual(order.items.get().quantity, 1)
        self.assertEqual(self.variant.stock, 4)
        self.assertEqual(self.client.session["cart"], {})

    def test_failure_and_cancellation_remain_pending_and_retryable(self):
        for outcome, state in (("failed", "failure"), ("cancelled", "cancelled")):
            with self.subTest(outcome=outcome):
                order = self.start(self.order())
                action = self.client.post(reverse("orders:local_payment_simulator", args=[order.pk]), {"outcome": outcome})
                result = self.client.get(action.url)
                order.refresh_from_db()
                self.assertContains(result, f'data-result-state="{state}"')
                self.assertFalse(order.is_paid)
                self.assertTrue(order.can_start_online_payment)

    def test_simulator_enforces_ownership_and_post(self):
        order = self.start(self.order())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("orders:local_payment_simulator", args=[order.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("orders:local_payment_simulator", args=[order.pk]), {"outcome": "success"}).status_code, 404)

    def test_csrf_protects_outcome_post(self):
        order = self.order()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(reverse("orders:local_payment_simulator", args=[order.pk]), {"outcome": "success"})
        self.assertEqual(response.status_code, 403)


@override_settings(LOCAL_PAYMENT_SIMULATOR_ENABLED=False, MOCK_PAYMENT_SIMULATION_ENABLED=False)
class LocalPaymentSimulatorDisabledTests(TestCase):
    def test_disabled_simulator_gateway_cannot_be_started(self):
        user = get_user_model().objects.create_user("simulator-disabled", password="secret")
        order = Order.objects.create(user=user, full_name="Disabled", address="1 Test Way", city="Tehran", phone="123", payment_method=Order.PaymentMethod.MOCK, currency=Currency.USD, total_amount=Decimal("20.00"))
        self.client.force_login(user)
        self.assertNotIn(Order.PaymentMethod.MOCK, dict(get_payment_choices(Currency.USD)))
        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]), fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertFalse(order.is_paid)
