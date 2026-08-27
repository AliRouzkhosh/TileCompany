import inspect
import json
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from products.catalog_domain import calculate_variant_combination_signature
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    Product,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    ProductView,
    VariantAttributeValue,
    VariantPrice,
)
from products.variant_selector import _variant_detail_specifications
from reviews.models import Review


class ProductDetailDOMAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.nested_forms = 0
        self.ids = []
        self.form_ids = set()
        self.form_targets = []
        self.label_targets = []
        self.aria_controls = []
        self.attributes_by_id = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ids.append(element_id)
            self.attributes_by_id[element_id] = attributes
        if attributes.get("form"):
            self.form_targets.append(attributes["form"])
        if attributes.get("aria-controls"):
            self.aria_controls.extend(attributes["aria-controls"].split())
        if tag == "label" and attributes.get("for"):
            self.label_targets.append(attributes["for"])
        if tag == "form":
            if self.form_depth:
                self.nested_forms += 1
            self.form_depth += 1
            if element_id:
                self.form_ids.add(element_id)

    def handle_endtag(self, tag):
        if tag == "form" and self.form_depth:
            self.form_depth -= 1


class StorefrontV4DFinalProductDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product_type = ProductType.objects.create(
            code="final-qa-v4d", name="Final QA Product", name_fa="محصول کنترل نهایی"
        )
        cls.category = Category.objects.create(
            name="Long customer-facing category for final QA",
            name_fa="دسته‌بندی طولانی برای کنترل نهایی محصول",
            slug="final-qa-category-v4d",
        )
        cls.product = Product.objects.create(
            product_type=cls.product_type,
            name="A deliberately long configurable product name for final purchase QA",
            name_fa="نام بلند محصول پیکربندی‌پذیر برای کنترل نهایی فرایند خرید",
            slug="final-product-detail-v4d",
            sku="FINAL-PRODUCT-DETAIL-V4D",
            description="First paragraph.\n\nSecond paragraph with enough content to preserve the no-JS reading state.",
            description_fa="بند نخست.\n\nبند دوم برای بررسی محتوای بدون جاوااسکریپت.",
            is_active=True,
        )
        cls.product.categories.add(cls.category)
        definition = AttributeDefinition.objects.create(
            code="configuration_profile_v4d",
            name="Configuration profile",
            name_fa="پیکربندی محصول",
            data_type="option",
        )
        cls.binding = ProductTypeAttribute.objects.create(
            product_type=cls.product_type,
            definition=definition,
            scope="variant",
            is_required=True,
            is_variant_dimension=True,
            show_on_storefront=True,
            show_in_specifications=True,
            include_in_purchase_snapshot=True,
            sort_order=1,
        )
        cls.variants = []
        states = (
            ("standard", 5, ProductVariant.PriceUnit.BOX, "100.00", "120.00"),
            ("single", 1, ProductVariant.PriceUnit.UNIT, "125.00", None),
            ("unavailable", 0, ProductVariant.PriceUnit.M2, "150.00", None),
        )
        for index, (code, stock, unit, amount, compare_at) in enumerate(states, start=1):
            option = AttributeOption.objects.create(
                definition=definition,
                code=code,
                name=code.title(),
                name_fa=f"گزینهٔ {index}",
                sort_order=index,
            )
            variant = ProductVariant.objects.create(
                product=cls.product,
                sku=f"FINAL-VARIANT-{index}-WITH-LARGE-SKU",
                price_unit=unit,
                stock=stock,
                is_active=True,
                sort_order=index,
            )
            VariantAttributeValue.objects.create(
                variant=variant, binding=cls.binding, option_value=option
            )
            variant.combination_signature = calculate_variant_combination_signature(variant)
            variant.save(update_fields=["combination_signature"])
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.USD,
                amount=Decimal(amount),
                compare_at_amount=Decimal(compare_at) if compare_at else None,
            )
            cls.variants.append(variant)

    def detail_url(self, variant=None, language="en"):
        variant = variant or self.variants[0]
        return (
            reverse("products:product_detail", args=[self.product.slug])
            + f"?variant={variant.pk}"
        ).replace("/en/", f"/{language}/", 1)

    def detail(self, variant=None, language="en", client=None):
        return (client or self.client).get(self.detail_url(variant, language))

    def source(self, relative_path):
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_complete_initial_state_and_dom_relationships_are_valid(self):
        response = self.detail()
        parser = ProductDetailDOMAudit()
        parser.feed(response.content.decode())
        duplicates = {item for item in parser.ids if parser.ids.count(item) > 1}
        all_ids = set(parser.ids)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(parser.nested_forms, 0)
        self.assertEqual(duplicates, set())
        self.assertTrue(set(parser.form_targets).issubset(parser.form_ids))
        self.assertTrue(set(parser.label_targets).issubset(all_ids))
        self.assertTrue(set(parser.aria_controls).issubset(all_ids))
        self.assertEqual(parser.ids.count("addToCartForm"), 1)
        self.assertEqual(parser.ids.count("buyNowForm"), 1)
        self.assertContains(response, 'form="addToCartForm"', count=1)
        self.assertEqual(
            parser.attributes_by_id["resolvedVariantId"].get("value"),
            str(self.variants[0].pk),
        )
        self.assertEqual(
            parser.attributes_by_id["cartVariantInput"].get("value"),
            str(self.variants[0].pk),
        )
        self.assertEqual(
            parser.attributes_by_id["buyVariantInput"].get("value"),
            str(self.variants[0].pk),
        )
        self.assertContains(response, "$100.00")
        self.assertContains(response, "$120.00")
        self.assertContains(response, "FINAL-VARIANT-1-WITH-LARGE-SKU")
        self.assertContains(response, "5 boxes available")

    def test_actual_add_to_cart_and_buy_now_keep_exact_variant_and_quantity(self):
        add_client = Client()
        next_url = self.detail_url(self.variants[0])
        add_response = add_client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.variants[0].pk, "quantity": 2, "next": next_url},
        )
        self.assertRedirects(add_response, next_url, fetch_redirect_response=False)
        self.assertEqual(
            add_client.session["cart"], {f"v_{self.variants[0].pk}": 2}
        )

        buy_client = Client()
        buy_response = buy_client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.variants[1].pk, "quantity": 1, "buy_now": "1"},
        )
        self.assertRedirects(
            buy_response, reverse("orders:checkout_shipping"), fetch_redirect_response=False
        )
        self.assertEqual(
            buy_client.session["cart"], {f"v_{self.variants[1].pk}": 1}
        )

    def test_zero_stock_and_quantity_clamp_contracts_remain_exact(self):
        ProductVariant.objects.filter(pk__in=[variant.pk for variant in self.variants[:2]]).update(
            is_active=False
        )
        response = self.detail(self.variants[2])
        self.assertContains(response, 'id="quantityInput"')
        self.assertContains(response, 'value="0"\n                min="0"\n                max="0"')
        self.assertContains(response, 'id="addToCartButton"\n            type="submit"\n            disabled')
        self.assertContains(response, 'id="buyNowButton"\n            type="submit"\n            disabled')
        quantity = self.source("static/js/quantity.js")
        self.assertIn("Math.max(minimum, Math.min(maximum, value))", quantity)
        selector = self.source("static/js/variant_selector.js")
        self.assertIn("Math.min(variant.stock, Math.max(1", selector)
        self.assertIn("elements.qty.min = variant.stock < 1 ? 0 : 1", selector)

    def test_no_js_server_render_is_truthful_and_purchase_capable(self):
        response = self.detail(language="fa")
        for contract in (
            "data-gallery-fallback",
            "data-product-commerce-summary",
            "data-selected-configuration-values",
            "data-product-variant-sku",
            "data-selected-variant-specifications",
            "data-product-description",
            'id="quantityInput"',
            'id="addToCartForm"',
            'id="buyNowForm"',
        ):
            self.assertContains(response, contract)
        self.assertContains(response, "FINAL-VARIANT-1-WITH-LARGE-SKU")
        self.assertContains(response, "گزینهٔ 1")

    def test_one_resolved_variant_boundary_owns_all_dynamic_consumers(self):
        selector = self.source("static/js/variant_selector.js")
        actions = self.source("static/js/product_action.js")
        gallery = self.source("static/js/product_gallery.js")
        self.assertEqual(selector.count('new CustomEvent("storefront:variant-resolved"'), 1)
        for contract in (
            "elements.price.textContent",
            "elements.originalPrice",
            "elements.discount",
            "elements.priceUnit",
            "elements.stock",
            "elements.qty.max = variant.stock",
            "elements.hiddenVariant.value = variant.id",
            "updateVariantDetails(variant)",
            "publishResolvedVariant(variant)",
        ):
            self.assertIn(contract, selector)
        self.assertIn('document.addEventListener("storefront:variant-resolved"', actions)
        self.assertIn('document.addEventListener("storefront:variant-resolved"', gallery)
        self.assertIn("rail.replaceChildren", gallery)
        self.assertIn("Array.from(new Set", gallery)

    def test_tabs_gallery_related_and_purchase_affordance_keep_frozen_contracts(self):
        tabs = self.source("static/js/tabs.js")
        gallery = self.source("static/js/product_gallery.js")
        action = self.source("static/js/product_action.js")
        related = self.source("products/templates/products/components/related_products.html")
        styles = self.source("theme/static_src/src/styles.css")
        for contract in ("ArrowRight", "ArrowLeft", "Home", "End", "hashchange"):
            self.assertIn(contract, tabs)
        self.assertIn('gallery.addEventListener("keydown"', gallery)
        self.assertNotIn('document.addEventListener("keydown"', gallery)
        self.assertIn('products/components/product_card.html', related)
        self.assertIn("clamp(11.25rem, 51vw, 12.8125rem)", styles)
        self.assertIn("clamp(16.25rem, 21vw, 19rem)", styles)
        for state in (
            "eligibilityReached",
            "mainActionVisible",
            "mainActionSeen",
            "footerVisible",
        ):
            self.assertIn(state, action)
        self.assertIn('mobileBar.toggleAttribute("inert", !visible)', action)
        self.assertIn('window.matchMedia("(min-width: 1024px)")', action)
        self.assertIn('desktopMedia.addEventListener?.("change", paintMobileBar)', action)

    def test_no_one_and_multiple_review_query_counts_remain_bounded(self):
        self.detail()
        with CaptureQueriesContext(connection) as no_review_queries:
            self.detail()

        users = []
        for index in range(3):
            user = get_user_model().objects.create_user(
                username=f"reviewer-v4d-{index}", password=None
            )
            users.append(user)
            Review.objects.create(
                product=self.product,
                user=user,
                rating=index + 3,
                comment=f"Final QA review {index}",
                is_approved=True,
            )
        self.detail()
        with CaptureQueriesContext(connection) as multiple_review_queries:
            response = self.detail()

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(no_review_queries), 35)
        self.assertLessEqual(len(multiple_review_queries), 35)

    def test_prior_browsing_history_does_not_add_dead_detail_queries(self):
        history_client = Client()
        history_client.get(self.detail_url())
        historical_product = Product.objects.create(
            product_type=self.product_type,
            name="Previously viewed product that is not rendered on Product Detail",
            slug="previously-viewed-not-rendered-v4d",
            sku="PREVIOUSLY-VIEWED-NOT-RENDERED-V4D",
            is_active=True,
        )
        ProductView.objects.create(
            product=historical_product,
            session_key=history_client.session.session_key,
        )

        with CaptureQueriesContext(connection) as queries:
            response = history_client.get(self.detail_url())

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("recently_viewed", response.context)
        self.assertLessEqual(len(queries), 35)

    def test_variant_payload_and_generic_source_are_bounded(self):
        response = self.detail()
        payload = json.dumps(
            response.context["variants_data"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertLess(len(payload), 20_000)
        generic_source = inspect.getsource(_variant_detail_specifications).lower()
        for forbidden in (
            "tile", "mobile", "laptop", "brand", "material", "design", "usage",
            "color", "size", "finish", "ram", "storage", "cpu", "gpu",
        ):
            self.assertNotIn(forbidden, generic_source)


class StorefrontV4DGenericSchemaStressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product_type = ProductType.objects.create(
            code="many-dimensions-v4d", name="Many Dimensions"
        )
        cls.product = Product.objects.create(
            product_type=cls.product_type,
            name="Generic Five Dimension Product",
            slug="generic-five-dimension-v4d",
            sku="GENERIC-FIVE-DIMENSION-V4D",
            is_active=True,
        )
        bindings = []
        option_sets = []
        for dimension_index in range(5):
            definition = AttributeDefinition.objects.create(
                code=f"dimension_{dimension_index}_v4d",
                name=f"Dimension {dimension_index + 1}",
                name_fa=f"ویژگی طولانی شمارهٔ {dimension_index + 1}",
                data_type="option",
            )
            binding = ProductTypeAttribute.objects.create(
                product_type=cls.product_type,
                definition=definition,
                scope="variant",
                is_required=True,
                is_variant_dimension=True,
                show_on_storefront=True,
                show_in_specifications=True,
                include_in_purchase_snapshot=True,
                sort_order=dimension_index + 1,
            )
            option_count = 12 if dimension_index == 0 else 1
            options = [
                AttributeOption.objects.create(
                    definition=definition,
                    code=f"option_{dimension_index}_{option_index}_v4d",
                    name=f"Naturally wrapping option {option_index + 1}",
                    name_fa=f"گزینهٔ دارای برچسب بلند شمارهٔ {option_index + 1}",
                    sort_order=option_index + 1,
                )
                for option_index in range(option_count)
            ]
            bindings.append(binding)
            option_sets.append(options)

        for variant_index in range(12):
            variant = ProductVariant.objects.create(
                product=cls.product,
                sku=f"MANY-DIMENSIONS-{variant_index + 1}",
                price_unit=ProductVariant.PriceUnit.TILE,
                stock=variant_index + 1,
                is_active=True,
                sort_order=variant_index + 1,
            )
            for dimension_index, binding in enumerate(bindings):
                option = (
                    option_sets[dimension_index][variant_index]
                    if dimension_index == 0
                    else option_sets[dimension_index][0]
                )
                VariantAttributeValue.objects.create(
                    variant=variant, binding=binding, option_value=option
                )
            variant.combination_signature = calculate_variant_combination_signature(variant)
            variant.save(update_fields=["combination_signature"])
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.USD,
                amount=Decimal(50 + variant_index),
            )
        cls.first_variant = cls.product.variants.order_by("sort_order").first()

    def test_five_groups_many_options_and_long_persian_labels_render_generically(self):
        response = self.client.get(
            f"/fa/product/{self.product.slug}/", {"variant": self.first_variant.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="variant-group"', count=5)
        self.assertContains(response, 'class="variant-btn ', count=16)
        self.assertContains(response, "min-h-11")
        self.assertContains(response, "گزینهٔ دارای برچسب بلند شمارهٔ 12")
        # The public SKU is now allowed to wrap rather than being ellipsized.
        self.assertContains(response, "break-words", count=17)
        self.assertEqual(len(response.context["variants_data"]), 12)
        payload = json.dumps(
            response.context["variants_data"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.assertLess(len(payload), 100_000)
