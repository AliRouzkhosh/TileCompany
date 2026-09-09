from decimal import Decimal
from pathlib import Path
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import Address
from products.catalog_domain import calculate_variant_combination_signature
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)


class StorefrontV5BCheckoutShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("v5b-buyer", password="secret")
        cls.address = Address.objects.create(
            user=cls.user,
            label="Checkout fixture",
            first_name="Checkout",
            last_name="Buyer",
            phone="0000000000",
            country="Iran",
            province="Tehran",
            city="Tehran",
            street_address="Fixture street",
            postal_code="0000000000",
            is_default_shipping=True,
        )
        cls.product_type = ProductType.objects.create(code="configurable-v5b", name="Configurable")
        definition = AttributeDefinition.objects.create(
            code="capacity_v5b", name="Capacity", name_fa="گنجایش", data_type="option"
        )
        cls.binding = ProductTypeAttribute.objects.create(
            product_type=cls.product_type,
            definition=definition,
            scope="variant",
            is_variant_dimension=True,
            is_required=True,
            show_on_storefront=True,
            show_in_specifications=True,
            include_in_purchase_snapshot=True,
            sort_order=1,
        )
        options = [
            AttributeOption.objects.create(definition=definition, code="small-v5b", name="Small", name_fa="کوچک"),
            AttributeOption.objects.create(definition=definition, code="large-v5b", name="Large", name_fa="بزرگ"),
        ]
        products = [
            Product.objects.create(
                product_type=cls.product_type,
                name="Prepared image product",
                name_fa="محصول تصویردار",
                slug="prepared-image-v5b",
                sku="IMAGE-V5B",
                image="products/v5b-fixture.jpg",
                is_active=True,
            ),
            Product.objects.create(
                product_type=cls.product_type,
                name="Prepared fallback product",
                name_fa="محصول بدون تصویر",
                slug="prepared-fallback-v5b",
                sku="FALLBACK-V5B",
                is_active=True,
            ),
        ]
        cls.variants = []
        for index, (product, option) in enumerate(zip(products, options), 1):
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"V5B-{index}",
                stock=8,
                price_unit=ProductVariant.PriceUnit.UNIT,
                is_active=True,
            )
            VariantAttributeValue.objects.create(
                variant=variant, binding=cls.binding, option_value=option
            )
            variant.combination_signature = calculate_variant_combination_signature(variant)
            variant.save(update_fields=["combination_signature"])
            VariantPrice.objects.create(
                variant=variant, currency=Currency.USD, amount=Decimal(str(20 * index))
            )
            cls.variants.append(variant)

    def setUp(self):
        self.client.force_login(self.user)

    def _set_checkout(self, count=1):
        session = self.client.session
        session["storefront_currency"] = Currency.USD
        session["cart"] = {f"v_{variant.pk}": index + 1 for index, variant in enumerate(self.variants[:count])}
        session["checkout_shipping"] = {
            "address_id": self.address.pk,
            "shipping_method": "standard",
        }
        session["checkout_payment"] = {
            "payment_method": "mock",
            "coupon_code": "",
            "email_receipt_requested": False,
        }
        session.save()

    def _response(self, step):
        return self.client.get(reverse(f"orders:checkout_{step}"))

    def test_shipping_payment_review_share_one_shell_and_one_h1(self):
        self._set_checkout()
        for step in ("shipping", "payment", "review"):
            response = self._response(step)
            html = response.content.decode()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
            self.assertIn('data-checkout-page', html)
            self.assertIn(f'data-checkout-step="{step}"', html)
            self.assertIn('data-checkout-content', html)
            self.assertIn('data-checkout-summary-column', html)

    def test_progress_has_truthful_three_step_order_and_states(self):
        self._set_checkout()
        expectations = {"shipping": "1", "payment": "2", "review": "3"}
        for step, value in expectations.items():
            html = self._response(step).content.decode()
            progress = html.split('data-checkout-progress', 1)[1].split('</nav>', 1)[0]
            desktop = progress.split('data-checkout-progress-desktop', 1)[1]
            self.assertLess(desktop.index("Shipping"), desktop.index("Payment"))
            self.assertLess(desktop.index("Payment"), desktop.index("Review"))
            self.assertIn(f'aria-valuenow="{value}"', progress)
            self.assertIn('aria-valuemax="3"', progress)
            self.assertIn('aria-current="step"', progress)
            self.assertNotIn(">Complete<", progress)
            self.assertNotIn(">Cart<", progress)

    def test_summary_uses_prepared_generic_lines_quantity_units_and_totals(self):
        self._set_checkout(2)
        response = self._response("payment")
        html = response.content.decode()
        self.assertContains(response, "Capacity: Small")
        self.assertContains(response, "Capacity: Large")
        self.assertContains(response, "1 item")
        self.assertContains(response, "2 items")
        self.assertContains(response, "$20.00")
        self.assertContains(response, "$80.00")
        self.assertContains(response, "$100.00")
        self.assertIn('data-checkout-line-image', html)
        self.assertIn('data-checkout-line-fallback', html)
        self.assertEqual(html.count('h-16 w-16'), 2)
        for variant in self.variants:
            self.assertIn(f'data-variant-id="{variant.pk}"', html)

    def test_shipping_financial_state_is_truthful_and_later_zero_is_authoritative(self):
        self._set_checkout()
        self.assertContains(self._response("shipping"), "Confirmed after this step")
        self.assertContains(self._response("payment"), "No charge")
        self.assertContains(self._response("review"), "No charge")

    def test_responsive_sticky_summary_and_bounded_line_list_contract(self):
        root = Path(settings.BASE_DIR)
        base = (root / "orders/templates/orders/checkout_base.html").read_text(encoding="utf-8")
        summary = (root / "orders/templates/orders/components/checkout_summary.html").read_text(encoding="utf-8")
        self.assertIn("lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)]", base)
        self.assertIn("lg:sticky lg:top-24", base)
        self.assertIn("max-h-72", summary)
        self.assertIn("overflow-y-auto", summary)
        self.assertNotIn("<details", summary)

    def test_confidence_content_is_compact_and_uses_supported_copy(self):
        source = (
            Path(settings.BASE_DIR) / "orders/templates/orders/components/trust_badges.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Secure checkout", source)
        self.assertIn("Stock is reserved after the order is placed", source)
        self.assertIn("Customer support before and after purchase", source)
        self.assertNotIn("Every order is protected", source)
        self.assertNotIn("Easy returns", source)

    def test_checkout_templates_do_not_read_orm_or_branch_by_product_type(self):
        root = Path(settings.BASE_DIR)
        paths = (
            "orders/templates/orders/checkout_base.html",
            "orders/templates/orders/components/checkout_steps.html",
            "orders/templates/orders/components/checkout_summary.html",
            "orders/templates/orders/components/trust_badges.html",
        )
        source = "\n".join((root / path).read_text(encoding="utf-8").lower() for path in paths)
        self.assertNotRegex(source, r"\.(?:all|filter|children|variants|attribute_values)\b")
        self.assertNotRegex(source, r"(?:product_type|definition_code)\s*(?:==|===)")
        self.assertNotRegex(source, r"{%\s*if[^%]*(?:brand|material|design|usage|color|size|finish|ram|storage|cpu|gpu)")

    def test_checkout_query_counts_are_bounded_and_line_count_independent(self):
        # F3H evaluates the maintained Dashboard capability set for the
        # shared Storefront Dashboard-link contract. Django's standard auth
        # backend loads direct and Group permissions once per request; this
        # is independent of checkout line count.
        # The dynamic commerce contract adds one singleton-policy read and one
        # bounded delivery-method-policy read per request. Both are independent
        # of checkout line count and deliberately remain uncached across requests.
        expected_limits = {"shipping": 28, "payment": 27, "review": 28}
        results = {}
        for count in (1, 2):
            self._set_checkout(count)
            for step in ("shipping", "payment", "review"):
                self._response(step)
                with CaptureQueriesContext(connection) as queries:
                    self._response(step)
                results[(step, count)] = len(queries)
                self.assertLessEqual(len(queries), expected_limits[step])
        for step in expected_limits:
            self.assertEqual(results[(step, 1)], results[(step, 2)])
