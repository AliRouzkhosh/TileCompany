from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from orders.models import Order, OrderItem, OrderStatusHistory
from orders.selectors import get_customer_order_detail
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


class CustomerOrderDetailV6D1Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("v6d-owner", password="secret")
        self.other = get_user_model().objects.create_user("v6d-other", password="secret")
        self.client.force_login(self.user)

    def snapshot(self, name, configuration=()):
        return {
            "version": 2,
            "product": {"id": 1, "product_type": {"code": "generic", "label_default": "Generic", "label_fa": "عمومی"}, "sku": "HIST", "name_default": name, "name_fa": name},
            "variant": {"id": 1, "sku": "HIST-SKU", "combination_signature": "__base__", "selling_unit": {"code": "unit", "price_label_default": "Per Unit", "price_label_fa": "واحد", "singular_noun_default": "item", "singular_noun_fa": "عدد", "plural_noun_default": "items", "plural_noun_fa": "عدد"}},
            "attributes": {"product": [], "variant": [{"definition_code": f"c{index}", "label_default": "Configuration", "label_fa": "پیکربندی", "scope": "variant", "data_type": "text", "unit": None, "is_dimension": False, "sort_order": index, "values": [{"option_code": None, "raw_value": value, "display_default": value, "display_fa": value}]} for index, value in enumerate(configuration, 1)]},
            "operational": {},
        }

    def order(self, *, status=Order.Status.CONFIRMED, method=Order.PaymentMethod.COD, paid=False, user=None):
        return Order.objects.create(user=user or self.user, user_order_number=20, full_name="Snapshot Buyer", address="1 Historical Way", city="Tehran", phone="09120000000", status=status, payment_method=method, is_paid=paid, shipping_method=Order.ShippingMethod.EXPRESS, shipping_cost=Decimal("10"), total_amount=Decimal("110"))

    def item(self, order, name="Historic item", price="100", reference=None, quantity=1, product=None, variant=None, configuration=()):
        return OrderItem.objects.create(order=order, product=product, variant=variant, product_name=name, unit_price=Decimal(price), unit_reference_price=Decimal(reference) if reference else None, quantity=quantity, variant_snapshot=self.snapshot(name, configuration))

    def test_account_shell_status_payment_cod_and_ownership(self):
        retryable = self.order(status=Order.Status.PENDING, method=Order.PaymentMethod.MOCK)
        self.item(retryable)
        response = self.client.get(reverse("orders:order_detail", args=[retryable.pk]))
        self.assertContains(response, "account-shell")
        self.assertContains(response, "data-account-nav-selector")
        self.assertContains(response, 'data-order-payment-recovery')
        self.assertContains(response, reverse("orders:payment_start", args=[retryable.pk]))
        self.assertContains(response, "Order #20")

        cod = self.order(method=Order.PaymentMethod.COD)
        self.item(cod)
        cod_response = self.client.get(reverse("orders:order_detail", args=[cod.pk]))
        self.assertContains(cod_response, "Payment due on delivery")
        self.assertNotContains(cod_response, 'data-order-payment-recovery')
        foreign = self.order(user=self.other)
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[foreign.pk])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("orders:order_detail", args=[retryable.pk])).status_code, 302)

    def test_all_items_generic_thumbnail_and_historical_discount_pricing(self):
        order = self.order(paid=True)
        product = Product.objects.create(name="Mutable catalog title", slug="v6d-image", sku="V6D", image="products/recognition.jpg")
        self.item(order, "Historic Tile", "80", "100", 2, product=product, configuration=("60×60", "Matte"))
        self.item(order, "Historic Laptop", "30", "30", 1, configuration=("16 GB RAM", "1 TB SSD"))
        self.item(order, "Historic Accessory", "10", None, 1, configuration=("Bluetooth",))
        for index in range(9):
            self.item(order, f"Historic extra {index}", "1", None, 1)
        response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        html = response.content.decode()
        self.assertEqual(html.count('data-order-item'), 12)
        self.assertIn("Historic Tile", html)
        self.assertIn("Historic Laptop", html)
        self.assertIn("Historic Accessory", html)
        self.assertIn("products/recognition.jpg", html)
        self.assertNotIn("Mutable catalog title", html)
        self.assertIn("$80.00", html)
        self.assertIn("$100.00", html)
        self.assertNotIn("Items at original price", html)  # legacy-null line makes reference aggregate unknowable
        self.assertNotIn("Product discounts", html)  # mixed legacy/null-reference Order
        self.assertIn('order-detail__reference-price', html)
        self.assertIn("20% off", html)
        self.assertNotIn("You save", html)
        self.assertNotIn("VariantPrice", html)
        self.assertEqual(order.items.first().unit_reference_price, Decimal("100"))

    def test_complete_reference_history_restores_summary_without_promotional_copy(self):
        order = self.order(paid=True)
        order.total_amount = Decimal("250")
        order.save(update_fields=["total_amount"])
        self.item(order, "Discounted Tile", "80", "100", 2)
        self.item(order, "Discounted Laptop", "80", "100", 1)

        html = self.client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
        self.assertIn("Items at original price", html)
        self.assertIn("Product discounts", html)
        self.assertIn("$300.00", html)
        self.assertIn("−$60.00", html)
        self.assertIn("$240.00", html)
        self.assertIn("$250.00", html)
        self.assertNotIn("You save", html)

    def test_current_variant_price_change_cannot_change_historical_discount(self):
        order = self.order(paid=True)
        product = Product.objects.create(name="Current product", slug="historical-price", sku="HISTORICAL-PRICE")
        variant = ProductVariant.objects.create(product=product, sku="HISTORICAL-PRICE-1", stock=3)
        current_price = VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("900"), compare_at_amount=Decimal("1000"))
        self.item(order, "Historic price", "80", "100", product=product, variant=variant)
        url = reverse("orders:order_detail", args=[order.pk])
        before = self.client.get(url).content.decode()
        current_price.amount = Decimal("1")
        current_price.compare_at_amount = Decimal("2")
        current_price.save(update_fields=["amount", "compare_at_amount"])
        after = self.client.get(url).content.decode()
        for html in (before, after):
            self.assertIn("$80.00", html)
            self.assertIn("$100.00", html)
            self.assertIn("20% off", html)
            self.assertNotIn("$1.00", html)

    def test_safe_current_product_navigation_is_generic_and_inactive_products_are_unlinked(self):
        order = self.order(paid=True)
        active_tile = Product.objects.create(name="Current Tile", slug="detail-tile", sku="DETAIL-TILE", image="products/tile.jpg")
        active_laptop = Product.objects.create(name="Current Laptop", slug="detail-laptop", sku="DETAIL-LAPTOP")
        inactive_accessory = Product.objects.create(name="Hidden accessory", slug="detail-hidden", sku="DETAIL-HIDDEN", is_active=False)
        self.item(order, "Historic Tile", product=active_tile)
        self.item(order, "Historic Laptop", product=active_laptop)
        self.item(order, "Historic Accessory", product=inactive_accessory)
        self.item(order, "Missing current product")

        html = self.client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
        self.assertIn(reverse("products:product_detail", args=[active_tile.slug]), html)
        self.assertIn(reverse("products:product_detail", args=[active_laptop.slug]), html)
        self.assertNotIn(reverse("products:product_detail", args=[inactive_accessory.slug]), html)
        self.assertIn('aria-label="View current product: Historic Tile"', html)
        self.assertIn("Historic Accessory", html)
        self.assertIn("Missing current product", html)

    def test_persisted_history_and_bounded_query_count(self):
        small = self.order(paid=True)
        self.item(small)
        OrderStatusHistory.objects.create(order=small, status=Order.Status.CONFIRMED, note="Online payment confirmed.")
        large = self.order(paid=True)
        for index in range(12):
            self.item(large, f"Item {index}")

        def query_count(order):
            with CaptureQueriesContext(connection) as queries:
                detail = get_customer_order_detail(user=self.user, order_id=order.pk)
                for item in detail.items.all():
                    item.purchase_presentation.current_product_url
                    item.order_history_thumbnail_url
                list(detail.status_history.all())
            return len(queries)

        self.assertEqual(query_count(small), query_count(large))
        response = self.client.get(reverse("orders:order_detail", args=[small.pk]))
        self.assertContains(response, "Online payment confirmed.")

    def test_farsi_important_status_and_payment_copy_render(self):
        order = self.order(status=Order.Status.PENDING, method=Order.PaymentMethod.MOCK)
        self.item(order)
        OrderStatusHistory.objects.create(order=order, status=Order.Status.PENDING, note="Order created, awaiting payment.")
        with translation.override("fa"):
            response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(response, "پرداخت لازم است")
        self.assertContains(response, "تکمیل پرداخت")
        self.assertContains(response, "اقلام خریداری‌شده")
        self.assertContains(response, "قیمت واحد")
        self.assertContains(response, "جمع این کالا")
        self.assertContains(response, "خلاصهٔ سفارش")
        self.assertContains(response, "تحویل")
        self.assertContains(response, "پرداخت")
        self.assertContains(response, "سابقهٔ سفارش")
        self.assertNotContains(response, ">Line total<")
        self.assertNotContains(response, ">Unit price<")
        self.assertNotContains(response, "Order actions")
