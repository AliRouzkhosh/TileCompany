import inspect
import json
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from products.catalog_domain import calculate_variant_combination_signature
from products.catalog_read import read_product_detail
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)
from products.selectors import get_product_variant_context
from products.variant_selector import _variant_detail_specifications


class _ProductDetailStructureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.nested_forms = 0
        self.ids = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if tag == "form":
            if self.form_depth:
                self.nested_forms += 1
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form" and self.form_depth:
            self.form_depth -= 1


class StorefrontV4AProductDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product_type = ProductType.objects.create(
            code="portable-device-v4a", name="Portable Device", name_fa="دستگاه همراه"
        )
        cls.category = Category.objects.create(
            name="Portable Devices", name_fa="دستگاه‌های همراه", slug="portable-devices-v4a"
        )
        cls.product = Product.objects.create(
            product_type=cls.product_type,
            name="Foundation Device",
            name_fa="دستگاه پایه",
            slug="foundation-device-v4a",
            sku="FOUNDATION-DEVICE",
            description="A generic detail test product.",
            is_active=True,
        )
        cls.product.categories.add(cls.category)

        cls.product_bindings = {}
        for order, (code, label, label_fa, visible) in enumerate(
            (
                ("maker_identity", "Maker", "سازنده", True),
                ("construction_family", "Construction", "ساختار", True),
                ("intended_context", "Intended context", "کاربرد", True),
                ("internal_tracking", "Internal tracking", "ردیابی داخلی", False),
            ),
            start=1,
        ):
            definition = AttributeDefinition.objects.create(
                code=code, name=label, name_fa=label_fa, data_type="option"
            )
            binding = ProductTypeAttribute.objects.create(
                product_type=cls.product_type,
                definition=definition,
                scope="product",
                show_on_storefront=visible,
                show_in_specifications=True,
                show_on_product_card=visible,
                sort_order=order,
            )
            option = AttributeOption.objects.create(
                definition=definition,
                code=f"{code}-value",
                name=f"{label} value",
                name_fa=f"مقدار {label_fa}",
            )
            ProductAttributeValue.objects.create(
                product=cls.product, binding=binding, option_value=option
            )
            cls.product_bindings[code] = binding

        cls.variant_bindings = {}
        cls.variant_options = {}
        variant_schema = (
            ("appearance_tone", "Appearance", "ظاهر", True, True, ("dark", "light")),
            ("memory_band", "Memory", "حافظه", True, True, ("standard", "extended")),
            ("capacity_band", "Capacity", "ظرفیت", True, True, ("compact", "large")),
            ("service_term", "Service term", "مدت خدمات", False, True, ("basic", "premium")),
            ("diagnostic_code", "Diagnostic", "شناسهٔ داخلی", False, False, ("alpha", "beta")),
        )
        for order, (code, label, label_fa, dimension, visible, values) in enumerate(
            variant_schema, start=1
        ):
            definition = AttributeDefinition.objects.create(
                code=code, name=label, name_fa=label_fa, data_type="option"
            )
            binding = ProductTypeAttribute.objects.create(
                product_type=cls.product_type,
                definition=definition,
                scope="variant",
                is_required=dimension,
                is_variant_dimension=dimension,
                show_on_storefront=visible,
                show_in_specifications=True,
                include_in_purchase_snapshot=dimension,
                sort_order=order,
            )
            cls.variant_bindings[code] = binding
            cls.variant_options[code] = {
                value: AttributeOption.objects.create(
                    definition=definition,
                    code=value,
                    name=value.title(),
                    name_fa=f"{label_fa} {value}",
                    sort_order=position,
                )
                for position, value in enumerate(values, start=1)
            }

        cls.variants = []
        selections = (
            {
                "appearance_tone": "dark",
                "memory_band": "standard",
                "capacity_band": "compact",
                "service_term": "basic",
                "diagnostic_code": "alpha",
            },
            {
                "appearance_tone": "light",
                "memory_band": "extended",
                "capacity_band": "large",
                "service_term": "premium",
                "diagnostic_code": "beta",
            },
        )
        for index, selection in enumerate(selections, start=1):
            variant = ProductVariant.objects.create(
                product=cls.product,
                sku=f"FOUNDATION-{index}",
                price_unit=ProductVariant.PriceUnit.UNIT,
                stock=index * 4,
                is_active=True,
                sort_order=index,
            )
            for code, option_code in selection.items():
                VariantAttributeValue.objects.create(
                    variant=variant,
                    binding=cls.variant_bindings[code],
                    option_value=cls.variant_options[code][option_code],
                )
            variant.combination_signature = calculate_variant_combination_signature(variant)
            variant.save(update_fields=["combination_signature"])
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.USD,
                amount=Decimal(100 * index),
            )
            cls.variants.append(variant)

    def detail(self, variant=None, language="en"):
        variant = variant or self.variants[0]
        return self.client.get(
            f"/{language}/product/{self.product.slug}/", {"variant": variant.pk}
        )

    def test_product_details_use_all_visible_specifications_not_card_limit(self):
        response = self.detail()
        self.assertEqual(response.status_code, 200)
        presentation = response.context["product_presentation"]
        self.assertEqual(
            [item.definition_code for item in presentation.card_attributes],
            ["maker_identity", "construction_family"],
        )
        self.assertEqual(
            [item.definition_code for item in presentation.specification_attributes],
            ["maker_identity", "construction_family", "intended_context"],
        )
        self.assertNotContains(response, "Internal tracking")
        self.assertContains(response, "Portable Devices")
        self.assertContains(response, "Intended context value")

    def test_product_and_variant_values_are_localized_and_schema_ordered(self):
        response = self.detail(self.variants[1], language="fa")
        presentation = response.context["product_presentation"]
        self.assertEqual(presentation.category_labels, ("دستگاه‌های همراه",))
        self.assertEqual(
            [item.definition_code for item in presentation.specification_attributes],
            ["maker_identity", "construction_family", "intended_context"],
        )
        row = response.context["selected_variant_data"]
        self.assertEqual(row["id"], self.variants[1].pk)
        self.assertEqual(
            [item["definition_code"] for item in row["detail_specifications"]],
            ["appearance_tone", "memory_band", "capacity_band", "service_term"],
        )
        self.assertContains(response, "مدت خدمات premium")

    def test_each_variant_payload_has_exact_prepared_detail_state(self):
        response = self.detail(self.variants[0])
        rows = response.context["variants_data"]
        self.assertEqual({row["id"] for row in rows}, {item.pk for item in self.variants})
        first, second = rows
        self.assertEqual(first["sku"], "FOUNDATION-1")
        self.assertEqual(second["sku"], "FOUNDATION-2")
        self.assertEqual(
            [item["display_value"] for item in first["detail_specifications"]],
            ["Dark", "Standard", "Compact", "Basic"],
        )
        self.assertEqual(
            [item["display_value"] for item in second["detail_specifications"]],
            ["Light", "Extended", "Large", "Premium"],
        )
        self.assertEqual(first["operational_facts"], [])
        self.assertNotContains(response, "Diagnostic")
        self.assertEqual(response.context["selected_variant_data"]["id"], self.variants[0].pk)

    def test_zero_dimension_product_uses_sku_without_empty_spec_groups(self):
        simple_type = ProductType.objects.create(code="simple-v4a", name="Simple")
        product = Product.objects.create(
            product_type=simple_type,
            name="Simple Product",
            slug="simple-product-v4a",
            sku="SIMPLE-PRODUCT-V4A",
            is_active=True,
        )
        variant = ProductVariant.objects.create(
            product=product,
            sku="SIMPLE-BASE-V4A",
            combination_signature="__base__",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=2,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.USD, amount=Decimal("25")
        )
        response = self.client.get(
            reverse("products:product_detail", args=[product.slug]), {"variant": variant.pk}
        )
        row = response.context["selected_variant_data"]
        self.assertEqual(row["detail_specifications"], [])
        self.assertContains(response, "SIMPLE-BASE-V4A")
        self.assertContains(response, "data-selected-variant-rows hidden")
        self.assertContains(response, "data-variant-operational-section hidden")

    def test_tile_operational_facts_are_prepared_per_exact_variant(self):
        tile_type = ProductType.objects.create(code="tile", name="Tile")
        product = Product.objects.create(
            product_type=tile_type,
            name="Operational Product",
            slug="operational-product-v4a",
            sku="OPERATIONAL-PRODUCT",
            series_name="Series One",
            is_active=True,
        )
        definition = AttributeDefinition.objects.create(
            code="format_choice_v4a", name="Format", data_type="option"
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=tile_type,
            definition=definition,
            scope="variant",
            is_required=True,
            is_variant_dimension=True,
            include_in_purchase_snapshot=True,
            sort_order=1,
        )
        rows = []
        for index, value in enumerate(("compact", "wide"), start=1):
            option = AttributeOption.objects.create(
                definition=definition, code=value, name=value.title(), sort_order=index
            )
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"OPERATIONAL-{index}",
                stock=5,
                thickness_mm=Decimal(f"{index + 7}.50"),
                coverage_per_box_m2=Decimal(f"{index}.250"),
                tiles_per_box=index + 2,
                price_unit=ProductVariant.PriceUnit.BOX,
                is_active=True,
            )
            VariantAttributeValue.objects.create(
                variant=variant, binding=binding, option_value=option
            )
            variant.combination_signature = calculate_variant_combination_signature(variant)
            variant.save(update_fields=["combination_signature"])
            VariantPrice.objects.create(
                variant=variant, currency=Currency.USD, amount=Decimal(10 * index)
            )
        context = get_product_variant_context(
            product, RequestFactory().get("/"), Currency.USD
        )
        rows = context["variants_data"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["operational_facts"], rows[1]["operational_facts"])
        self.assertEqual(
            [item["label"] for item in rows[0]["operational_facts"]],
            ["Series", "Thickness", "Coverage", "Tiles per box"],
        )

    def test_purchase_dom_has_independent_forms_unique_ids_and_initial_state(self):
        response = self.detail(self.variants[1])
        parser = _ProductDetailStructureParser()
        parser.feed(response.content.decode())
        duplicates = {item for item in parser.ids if parser.ids.count(item) > 1}
        self.assertEqual(parser.nested_forms, 0)
        self.assertEqual(duplicates, set())
        self.assertNotContains(response, 'id="purchaseForm"')
        self.assertNotContains(response, 'id="selectedVariant"')
        self.assertContains(response, 'id="resolvedVariantId"', count=1)
        self.assertContains(response, 'id="addToCartForm"', count=1)
        self.assertContains(response, 'id="buyNowForm"', count=1)
        self.assertContains(
            response,
            f'name="variant_id"\n            id="cartVariantInput"\n            data-purchase-variant-input\n            value="{self.variants[1].pk}"',
        )
        self.assertContains(
            response,
            f'name="variant_id"\n            id="buyVariantInput"\n            data-purchase-variant-input\n            value="{self.variants[1].pk}"',
        )

    def test_zero_stock_keeps_exact_variant_and_disables_both_actions(self):
        variant = self.variants[0]
        variant.stock = 0
        variant.save(update_fields=["stock"])
        response = self.detail(variant)
        self.assertEqual(response.context["selected_variant_data"]["id"], variant.pk)
        self.assertContains(response, 'id="addToCartButton"\n            type="submit"\n            disabled')
        self.assertContains(response, 'id="buyNowButton"\n            type="submit"\n            disabled')
        self.assertContains(response, 'max="0"')

    def test_tab_roles_relationships_hash_and_keyboard_contract(self):
        response = self.detail()
        self.assertContains(response, 'role="tablist"', count=1)
        self.assertContains(response, 'role="tab"', count=3)
        self.assertContains(response, 'role="tabpanel"', count=3)
        for name in ("description", "specs", "reviews"):
            self.assertContains(response, f'aria-controls="{name}"')
            self.assertContains(response, f'id="{name}" role="tabpanel"')
            self.assertContains(response, f'aria-labelledby="product-tab-{name}"')
        tabs = (Path(settings.BASE_DIR) / "static/js/tabs.js").read_text(encoding="utf-8")
        for contract in (
            'event.key === "ArrowRight"',
            'event.key === "ArrowLeft"',
            'event.key === "Home"',
            'event.key === "End"',
            'button.setAttribute("aria-selected", String(active))',
            'panel.hidden = !active',
            'window.location.hash.slice(1)',
        ):
            self.assertIn(contract, tabs)

    def test_one_variant_event_updates_specs_and_both_purchase_forms(self):
        selector = (Path(settings.BASE_DIR) / "static/js/variant_selector.js").read_text(
            encoding="utf-8"
        )
        actions = (Path(settings.BASE_DIR) / "static/js/product_action.js").read_text(
            encoding="utf-8"
        )
        self.assertEqual(selector.count('new CustomEvent("storefront:variant-resolved"'), 1)
        self.assertIn("updateVariantDetails(variant)", selector)
        self.assertIn("publishResolvedVariant(variant)", selector)
        self.assertIn("variant?.detail_specifications", selector)
        self.assertIn("variant?.operational_facts", selector)
        self.assertIn('document.addEventListener("storefront:variant-resolved"', actions)
        self.assertIn('document.querySelectorAll("[data-purchase-variant-input]")', actions)
        self.assertIn('document.querySelectorAll("[data-purchase-quantity-input]")', actions)
        self.assertIn("button.disabled = !priced || variant.stock < 1", selector)
        self.assertIn("engine.best(variants, selected, button.dataset.group)", selector)

    def test_detail_payload_is_bounded_generic_and_templates_read_no_orm(self):
        with CaptureQueriesContext(connection) as captured:
            detail = read_product_detail(self.product)
        self.assertEqual(len(detail.variants), 2)
        self.assertLessEqual(len(captured), 7)

        response = self.detail()
        payload = json.dumps(
            response.context["variants_data"],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertLess(len(payload), 20_000)
        with self.assertNumQueries(0):
            html = render_to_string(
                "products/components/product_specs.html",
                {
                    "product_presentation": response.context["product_presentation"],
                    "selected_variant_data": response.context["selected_variant_data"],
                },
            )
        self.assertIn("Product details", html)

        generic_source = inspect.getsource(_variant_detail_specifications).lower()
        for forbidden in (
            "brand", "material", "design", "usage", "color", "size", "finish",
            "ram", "storage", "cpu", "gpu", "tile", "mobile", "laptop",
        ):
            self.assertNotIn(forbidden, generic_source)
