from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
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
    VariantAttributeValue,
    VariantPrice,
)
from products.test_storefront_v4a import _ProductDetailStructureParser


class StorefrontV4BPurchaseHeroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product_type = ProductType.objects.create(
            code="configurable-v4b", name="Configurable Product", name_fa="محصول پیکربندی‌پذیر"
        )
        cls.category = Category.objects.create(
            name="Devices", name_fa="دستگاه‌ها", slug="devices-v4b"
        )
        cls.product = Product.objects.create(
            product_type=cls.product_type,
            name="Conversion Device",
            name_fa="دستگاه خرید",
            slug="conversion-device-v4b",
            sku="CONVERSION-PRODUCT",
            is_active=True,
        )
        cls.product.categories.add(cls.category)
        definition = AttributeDefinition.objects.create(
            code="capacity_choice_v4b", name="Capacity", name_fa="ظرفیت", data_type="option"
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
        for index, (code, stock, amount, compare) in enumerate(
            (("compact", 7, "125.00", "150.00"), ("extended", 0, "180.00", None)),
            start=1,
        ):
            option = AttributeOption.objects.create(
                definition=definition,
                code=code,
                name=code.title(),
                name_fa=f"ظرفیت {index}",
                sort_order=index,
            )
            variant = ProductVariant.objects.create(
                product=cls.product,
                sku=f"CONVERSION-{index}",
                price_unit=ProductVariant.PriceUnit.UNIT,
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
                compare_at_amount=Decimal(compare) if compare else None,
            )
            cls.variants.append(variant)

    def detail(self, variant=None, language="en"):
        variant = variant or self.variants[0]
        return self.client.get(
            f"/{language}/product/{self.product.slug}/", {"variant": variant.pk}
        )

    def test_purchase_hero_renders_identity_commerce_and_configuration(self):
        response = self.detail()
        self.assertContains(response, 'data-product-hero')
        self.assertContains(response, 'data-product-gallery')
        self.assertContains(response, 'data-product-decision')
        self.assertContains(response, "Conversion Device")
        self.assertContains(response, "$125.00")
        self.assertContains(response, "$150.00")
        self.assertContains(response, "Per Unit")
        self.assertContains(response, "CONVERSION-1")
        self.assertContains(response, "7 items available")
        self.assertContains(response, 'data-selected-configuration-values')
        self.assertContains(response, "Compact")
        self.assertEqual(
            response.context["initial_gallery_images"],
            response.context["selected_variant_data"]["images"],
        )

    def test_gallery_has_stable_geometry_for_no_one_and_many_images(self):
        base_context = {"product": self.product}
        with self.assertNumQueries(0):
            empty = render_to_string(
                "products/components/product_gallery.html",
                {**base_context, "initial_gallery_images": []},
            )
            one = render_to_string(
                "products/components/product_gallery.html",
                {**base_context, "initial_gallery_images": ["/media/one.jpg"]},
            )
            many = render_to_string(
                "products/components/product_gallery.html",
                {**base_context, "initial_gallery_images": ["/media/one.jpg", "/media/two.jpg"]},
            )
        for html in (empty, one, many):
            self.assertIn("aspect-[4/3]", html)
            self.assertIn("data-gallery-main", html)
        self.assertIn("data-gallery-fallback", empty)
        self.assertIn("data-gallery-main-image", one)
        self.assertIn("data-gallery-thumbnails", many)
        self.assertEqual(many.count('class="gallery-thumb '), 2)

    def test_frozen_variant_hooks_and_contextual_states_are_preserved(self):
        response = self.detail()
        self.assertContains(response, 'data-group="capacity_choice_v4b"')
        self.assertContains(response, 'aria-pressed="false"')
        self.assertContains(response, 'data-context-incompatible="false"')
        selector = (Path(settings.BASE_DIR) / "static/js/variant_selector.js").read_text(encoding="utf-8")
        self.assertIn("engine.best(variants, selected, button.dataset.group)", selector)
        self.assertIn("engine.optionContextCompatible", selector)
        self.assertEqual(selector.count('new CustomEvent("storefront:variant-resolved"'), 1)
        generic_source = (
            Path(settings.BASE_DIR)
            / "products/templates/products/components/variant_selector.html"
        ).read_text(encoding="utf-8").lower()
        for forbidden in ("color", "size", "finish", "ram", "storage", "cpu", "gpu"):
            self.assertNotRegex(generic_source, rf'["\']{forbidden}["\']')

    def test_quantity_forms_and_zero_stock_state_remain_exact(self):
        response = self.detail(self.variants[1])
        parser = _ProductDetailStructureParser()
        parser.feed(response.content.decode())
        duplicates = {item for item in parser.ids if parser.ids.count(item) > 1}
        self.assertEqual(parser.nested_forms, 0)
        self.assertEqual(duplicates, set())
        self.assertContains(response, 'form="addToCartForm"', count=1)
        self.assertContains(response, 'data-mobile-purchase-button', count=1)
        self.assertContains(response, 'id="addToCartForm"', count=1)
        self.assertContains(response, 'id="buyNowForm"', count=1)
        self.assertContains(response, f'value="{self.variants[1].pk}"', count=3)
        self.assertContains(response, 'id="quantityInput"')
        self.assertContains(response, 'max="0"')
        self.assertContains(response, 'data-quantity-unit')
        self.assertContains(response, 'id="addToCartButton"\n            type="submit"\n            disabled')
        self.assertContains(response, 'id="buyNowButton"\n            type="submit"\n            disabled')

    def test_mobile_sticky_bar_reuses_form_and_event_without_third_form(self):
        actions = (Path(settings.BASE_DIR) / "static/js/product_action.js").read_text(encoding="utf-8")
        gallery = (Path(settings.BASE_DIR) / "static/js/product_gallery.js").read_text(encoding="utf-8")
        detail = self.detail().content.decode()
        self.assertIn('form="addToCartForm"', detail)
        self.assertIn("data-purchase-eligibility", detail)
        self.assertIn("data-purchase-affordance", detail)
        self.assertIn("IntersectionObserver", actions)
        self.assertIn("eligibilityReached", actions)
        self.assertIn("mainActionVisible", actions)
        self.assertIn("mainActionSeen", actions)
        self.assertIn("footerVisible", actions)
        self.assertIn('rootMargin: "0px 0px -50% 0px"', actions)
        self.assertIn("eligibilityObserver.observe(eligibilityAnchor)", actions)
        self.assertIn("visibilityObserver.observe(mainAction)", actions)
        self.assertIn("visibilityObserver.observe(footer)", actions)
        self.assertIn('mobileBar.toggleAttribute("inert", !visible)', actions)
        self.assertIn('document.addEventListener("storefront:variant-resolved"', actions)
        self.assertIn("mobileButton.disabled = Boolean(mainAction?.disabled)", actions)
        self.assertIn("document.querySelector(\"#productPrice\")?.textContent", actions)
        self.assertIn('gallery.addEventListener("keydown"', gallery)
        self.assertNotIn('document.addEventListener("keydown"', gallery)
        self.assertIn("event.detail?.variant?.images", gallery)

    def test_conversion_refinement_retains_hero_actions_and_option_targets(self):
        detail = self.detail().content.decode()
        info_source = (
            Path(settings.BASE_DIR)
            / "products/templates/products/components/product_info.html"
        ).read_text(encoding="utf-8")
        selector_source = (
            Path(settings.BASE_DIR)
            / "products/templates/products/components/variant_selector.html"
        ).read_text(encoding="utf-8")
        self.assertIn("data-product-hero", detail)
        self.assertIn("data-product-gallery", detail)
        self.assertIn("data-product-decision", detail)
        self.assertIn('id="addToCartForm"', detail)
        self.assertIn('id="buyNowForm"', detail)
        self.assertIn("data-selected-configuration", detail)
        self.assertIn("lg:space-y-3", info_source)
        self.assertIn("min-h-11", selector_source)
        self.assertNotRegex(info_source, r'data-product-decision[^>]*class="[^"]*sticky')

    def test_purchase_affordance_has_safe_compensation_and_one_form_authority(self):
        detail = self.detail().content.decode()
        page_source = (
            Path(settings.BASE_DIR) / "products/templates/products/product_detail.html"
        ).read_text(encoding="utf-8")
        action_source = (
            Path(settings.BASE_DIR)
            / "products/templates/products/components/product_action.html"
        ).read_text(encoding="utf-8")
        parser = _ProductDetailStructureParser()
        parser.feed(detail)
        self.assertEqual(parser.nested_forms, 0)
        self.assertIn('form="addToCartForm"', action_source)
        self.assertEqual(action_source.count("<form"), 2)
        self.assertIn("pb-[calc(5.5rem+env(safe-area-inset-bottom))]", page_source)
        self.assertIn("pb-[calc(0.5rem+env(safe-area-inset-bottom))]", action_source)
        self.assertIn("lg:inset-x-auto", action_source)

    def test_product_detail_query_growth_remains_bounded(self):
        self.detail()
        with CaptureQueriesContext(connection) as captured:
            response = self.detail()
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(captured), 40)
