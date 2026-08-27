from decimal import Decimal
from pathlib import Path
import re

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice


class StorefrontV5ACartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.image_product = Product.objects.create(
            name="Configurable fixture",
            slug="configurable-v5a",
            sku="CONFIG-V5A",
            image="products/v5a-fixture.jpg",
            series_name="Prepared operational fact",
            tiles_per_box=8,
            coverage_per_box_m2=Decimal("1.440"),
            is_active=True,
        )
        cls.fallback_product = Product.objects.create(
            name="Generic fallback fixture",
            slug="fallback-v5a",
            sku="FALLBACK-V5A",
            is_active=True,
        )
        cls.image_variant = ProductVariant.objects.create(
            product=cls.image_product,
            sku="CONFIG-V5A-A",
            stock=5,
            low_stock_threshold=2,
            price_unit=ProductVariant.PriceUnit.BOX,
            is_active=True,
        )
        cls.fallback_variant = ProductVariant.objects.create(
            product=cls.fallback_product,
            sku="FALLBACK-V5A-A",
            stock=3,
            price_unit=ProductVariant.PriceUnit.UNIT,
            is_active=True,
        )
        cls.batch_variant = ProductVariant.objects.create(
            product=cls.image_product,
            sku="CONFIG-V5A-B",
            stock=4,
            price_unit=ProductVariant.PriceUnit.BOX,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=cls.image_variant, currency=Currency.USD, amount=Decimal("25.00")
        )
        VariantPrice.objects.create(
            variant=cls.fallback_variant, currency=Currency.USD, amount=Decimal("12.00")
        )
        VariantPrice.objects.create(
            variant=cls.batch_variant, currency=Currency.USD, amount=Decimal("30.00")
        )

    def _set_cart(self, values):
        session = self.client.session
        session["cart"] = {f"v_{variant.pk}": quantity for variant, quantity in values}
        session["storefront_currency"] = Currency.USD
        session.save()

    def test_cart_line_uses_prepared_variant_identity_media_units_and_subtotal(self):
        self._set_cart(((self.image_variant, 2), (self.fallback_variant, 1)))
        response = self.client.get(reverse("orders:cart_detail"))

        self.assertContains(response, 'data-cart-product-image')
        self.assertContains(response, 'data-cart-product-fallback')
        self.assertContains(response, f'data-variant-id="{self.image_variant.pk}"')
        self.assertContains(response, "CONFIG-V5A-A")
        self.assertContains(response, "2 boxes")
        self.assertContains(response, "$25.00")
        self.assertContains(response, "$50.00")
        self.assertContains(response, "Packaging details")
        self.assertContains(response, reverse("products:product_detail", kwargs={"slug": self.image_product.slug}))

    def test_summary_remove_empty_and_responsive_contracts(self):
        self._set_cart(((self.image_variant, 1),))
        response = self.client.get(reverse("orders:cart_detail"))
        html = response.content.decode()

        self.assertIn(reverse("orders:checkout_shipping"), html)
        self.assertIn(reverse("orders:cart_remove", args=[f"v_{self.image_variant.pk}"]), html)
        self.assertIn("data-remove", html)
        self.assertIn("lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]", html)
        self.assertIn("lg:sticky lg:top-24", html)
        self.assertIn("h-[5.5rem]", html)
        self.assertIn("sm:h-[6.5rem]", html)
        self.assertNotIn("fixed bottom-", html)

        session = self.client.session
        session["cart"] = {}
        session.save()
        empty = self.client.get(reverse("orders:cart_detail"))
        self.assertContains(empty, "There are no products in your cart yet.")
        self.assertContains(empty, reverse("products:product_list"))

    def test_ajax_update_returns_authoritative_quantity_and_display_values(self):
        self._set_cart(((self.image_variant, 1),))
        response = self.client.post(
            reverse("orders:cart_update", args=[f"v_{self.image_variant.pk}"]),
            {"quantity": 99},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        payload = response.json()

        self.assertTrue(payload["success"])
        self.assertEqual(payload["quantity"], self.image_variant.stock)
        self.assertEqual(payload["cart_quantity"], self.image_variant.stock)
        self.assertEqual(payload["subtotal"], "125.00")
        self.assertEqual(payload["cart_total"], "125.00")
        self.assertEqual(payload["subtotal_display"], "$125.00")
        self.assertEqual(payload["cart_total_display"], "$125.00")
        self.assertEqual(self.client.session["cart"][f"v_{self.image_variant.pk}"], 5)

    def test_cart_dom_has_unique_ids_valid_scoped_forms_and_labels(self):
        self._set_cart(((self.image_variant, 2), (self.fallback_variant, 1)))
        html = self.client.get(reverse("orders:cart_detail")).content.decode()
        ids = re.findall(r'\bid="([^"]+)"', html)
        quantity_ids = re.findall(r'id="(cart-quantity-[^"]+)"', html)

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(quantity_ids), 2)
        for quantity_id in quantity_ids:
            self.assertIn(f'for="{quantity_id}"', html)
        self.assertNotRegex(html, r'<form[^>]*>\s*<form')
        self.assertEqual(html.count("data-cart-form"), 2)

    def test_cart_javascript_has_pending_restore_and_server_display_contract(self):
        source = (Path(settings.BASE_DIR) / "static/js/cart.js").read_text(encoding="utf-8")
        self.assertIn('form.dataset.pending', source)
        self.assertIn('restoreConfirmedQuantity(form)', source)
        self.assertIn('input.value = String(data.quantity)', source)
        self.assertIn('data.cart_total_display', source)
        self.assertIn('[data-cart-page-quantity]', source)
        self.assertIn('Intl.NumberFormat', source)
        self.assertLess(source.index('data.cart_total_display'), source.index('fallbackMoney'))

    def test_cart_source_has_no_product_type_business_branching(self):
        root = Path(settings.BASE_DIR)
        source = "\n".join(
            (root / path).read_text(encoding="utf-8").lower()
            for path in (
                "orders/templates/orders/cart_detail.html",
                "orders/templates/orders/components/cart_item.html",
                "orders/templates/orders/components/cart_summary.html",
                "static/js/cart.js",
            )
        )
        forbidden_branches = (
            r"{%\s*if[^%]*(?:brand|material|design|usage|color|size|finish|ram|storage|cpu|gpu)",
            r"(?:product_type|definition_code)\s*(?:==|===)",
        )
        for pattern in forbidden_branches:
            self.assertNotRegex(source, pattern)

    def test_cart_query_count_is_bounded_and_does_not_grow_per_line(self):
        url = reverse("orders:cart_detail")
        self._set_cart(((self.image_variant, 1),))
        self.client.get(url)
        with CaptureQueriesContext(connection) as one_line:
            self.client.get(url)

        self._set_cart(((self.image_variant, 1), (self.batch_variant, 1)))
        self.client.get(url)
        with CaptureQueriesContext(connection) as two_lines:
            self.client.get(url)

        self.assertLessEqual(len(one_line), 17)
        self.assertLessEqual(len(two_lines), 17)
