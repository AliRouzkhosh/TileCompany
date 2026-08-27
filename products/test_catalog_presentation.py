from decimal import Decimal
from pathlib import Path

from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import override

from .catalog_backfill import apply_tile_catalog
from .catalog_domain import calculate_variant_combination_signature
from .catalog_presentation import (
    GENERIC_SOURCE,
    LEGACY_FALLBACK_SOURCE,
    compose_product_detail_presentation,
    prepare_product_card_presentations,
)
from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    Size,
    Usage,
    VariantAttributeValue,
    VariantPrice,
)


class TilePresentationTestCase(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Atlas", name_fa="اطلس", slug="atlas"
        )
        self.materials = [
            Material.objects.create(
                name="Ceramic", name_fa="سرامیک", slug="ceramic"
            ),
            Material.objects.create(
                name="Glass", name_fa="شیشه", slug="glass"
            ),
        ]
        self.design = Design.objects.create(
            name="Stone", name_fa="سنگ", slug="stone"
        )
        self.usage = Usage.objects.create(
            name="Floor", name_fa="کف", slug="floor"
        )
        self.colors = [
            Color.objects.create(name="Black", name_fa="مشکی", slug="black"),
            Color.objects.create(name="White", name_fa="سفید", slug="white"),
        ]
        self.sizes = [
            Size.objects.create(
                name="30x30", name_fa="۳۰×۳۰", slug="30x30",
                length_mm=300, width_mm=300,
            ),
            Size.objects.create(
                name="60x60", name_fa="۶۰×۶۰", slug="60x60",
                length_mm=600, width_mm=600,
            ),
        ]
        self.finishes = [
            Finish.objects.create(name="Matte", name_fa="مات", slug="matte"),
            Finish.objects.create(name="Glossy", name_fa="براق", slug="glossy"),
        ]
        self.category = Category.objects.create(
            name="Floor Tiles", name_fa="کاشی کف", slug="floor-tiles-present"
        )
        self.product = Product.objects.create(
            name="Atlas Tile", name_fa="کاشی اطلس", slug="atlas-present",
            sku="ATLAS-PRESENT", brand=self.brand, is_active=True,
        )
        self.product.categories.add(self.category)
        self.product.materials.set(self.materials)
        self.product.designs.add(self.design)
        self.product.usages.add(self.usage)
        self.variants = []
        for index in range(2):
            variant = ProductVariant.objects.create(
                product=self.product,
                sku=f"ATLAS-PRESENT-{index + 1}",
                color=self.colors[index],
                size=self.sizes[index],
                finish=self.finishes[index],
                length_mm=self.sizes[index].length_mm,
                width_mm=self.sizes[index].width_mm,
                thickness_mm=Decimal("9.50"),
                tiles_per_box=4,
                coverage_per_box_m2=Decimal("1.440"),
                stock=10 + index,
                is_active=True,
                sort_order=index + 1,
            )
            VariantPrice.objects.create(
                variant=variant, currency="USD", amount=Decimal(20 + index)
            )
            self.variants.append(variant)

        self.no_brand_product = Product.objects.create(
            name="Unbranded Tile", slug="unbranded-present",
            sku="UNBRANDED-PRESENT", is_active=True,
        )
        self.no_brand_product.materials.add(self.materials[0])

        apply_tile_catalog()
        self.product.refresh_from_db()
        self.no_brand_product.refresh_from_db()
        self.variants = [
            ProductVariant.objects.get(pk=variant.pk) for variant in self.variants
        ]

    @staticmethod
    def codes(groups):
        return [group.definition_code for group in groups]

    def test_valid_tile_uses_generic_card_and_specification_bundles_with_stable_parity(self):
        stock_before = [variant.stock for variant in self.variants]
        price_before = list(
            VariantPrice.objects.filter(variant__in=self.variants)
            .order_by("variant_id")
            .values_list("variant_id", "currency", "amount")
        )
        prepare_product_card_presentations([self.product], language="en")
        card = self.product.catalog_presentation
        detail = compose_product_detail_presentation(
            self.product, selected_variant=self.variants[1], language="en"
        )

        self.assertEqual(card.source, GENERIC_SOURCE)
        self.assertEqual(self.codes(card.card_attributes), ["brand", "material"])
        self.assertLessEqual(len(card.card_attributes), 2)
        card_values = {
            group.definition_code: {value.value_code for value in group.values}
            for group in card.card_attributes
        }
        self.assertEqual(card_values["brand"], {self.brand.slug})
        self.assertEqual(card_values["material"], {item.slug for item in self.materials})
        self.assertEqual(
            self.codes(detail.specification_attributes),
            ["brand", "material", "design", "usage"],
        )
        self.assertEqual(
            [value.definition_code for value in detail.selected_variant_specifications],
            ["color", "size", "finish"],
        )
        self.assertEqual(
            {value.value_code for value in detail.selected_variant_specifications},
            {"white", "60x60", "glossy"},
        )
        self.assertEqual(
            [ProductVariant.objects.get(pk=item.pk).stock for item in self.variants],
            stock_before,
        )
        self.assertEqual(
            list(
                VariantPrice.objects.filter(variant__in=self.variants)
                .order_by("variant_id")
                .values_list("variant_id", "currency", "amount")
            ),
            price_before,
        )

    def test_card_template_renders_localized_generic_multiple_values_and_missing_brand(self):
        with override("en"):
            prepare_product_card_presentations([self.product], language="en")
            english = render_to_string(
                "products/components/product_card.html",
                {"product": self.product, "card_mode": "listing", "storefront_currency": "USD"},
            )
        self.assertIn("Brand", english)
        self.assertIn("Atlas", english)
        self.assertIn("Material", english)
        self.assertIn("Ceramic", english)
        self.assertIn("Glass", english)

        with override("fa"):
            prepare_product_card_presentations([self.product], language="fa")
            persian = render_to_string(
                "products/components/product_card.html",
                {"product": self.product, "card_mode": "listing", "storefront_currency": "USD"},
            )
        self.assertIn("برند", persian)
        self.assertIn("اطلس", persian)
        self.assertIn("جنس", persian)
        self.assertIn("سرامیک", persian)

        prepare_product_card_presentations([self.no_brand_product], language="en")
        unbranded = render_to_string(
            "products/components/product_card.html",
            {"product": self.no_brand_product, "card_mode": "listing", "storefront_currency": "USD"},
        )
        self.assertNotIn("Brand:", unbranded)
        self.assertIn("Material", unbranded)

    def test_specs_use_explicit_selected_variant_and_never_first_variant(self):
        selected = compose_product_detail_presentation(
            self.product, selected_variant=self.variants[1], language="en"
        )
        selected_html = render_to_string(
            "products/components/product_specs.html",
            {"product_presentation": selected},
        )
        self.assertIn("White", selected_html)
        self.assertIn("60x60", selected_html)
        self.assertIn("Glossy", selected_html)
        self.assertNotIn(">Black<", selected_html)
        self.assertNotIn(">30x30<", selected_html)
        self.assertNotIn(">Matte<", selected_html)

        no_selection = compose_product_detail_presentation(
            self.product, selected_variant=None, language="en"
        )
        no_selection_html = render_to_string(
            "products/components/product_specs.html",
            {"product_presentation": no_selection},
        )
        self.assertEqual(no_selection.selected_variant_specifications, ())
        self.assertNotIn("Glossy", no_selection_html)
        self.assertNotIn("Matte", no_selection_html)
        self.assertEqual(self.variants[1].length_mm, 600)
        self.assertEqual(self.variants[1].coverage_per_box_m2, Decimal("1.440"))

    def test_integrity_failure_logs_and_uses_one_complete_read_only_legacy_bundle(self):
        brand_binding = ProductTypeAttribute.objects.get(
            product_type=self.product.product_type,
            definition__code="brand",
        )
        brand_binding.is_required = True
        brand_binding.save(update_fields=["is_required"])
        ProductAttributeValue.objects.filter(
            product=self.product, binding=brand_binding
        ).delete()
        generic_material = AttributeOption.objects.get(
            definition__code="material", code="ceramic"
        )
        generic_material.name = "GENERIC-ONLY-NAME"
        generic_material.save(update_fields=["name"])
        counts_before = (
            ProductAttributeValue.objects.count(),
            VariantAttributeValue.objects.count(),
        )

        with self.assertLogs("products.catalog_presentation", level="ERROR") as captured:
            presentation = compose_product_detail_presentation(
                self.product, selected_variant=self.variants[1], language="en"
            )

        self.assertEqual(presentation.source, LEGACY_FALLBACK_SOURCE)
        self.assertTrue(presentation.used_fallback)
        self.assertEqual(
            self.codes(presentation.specification_attributes),
            ["brand", "material", "design", "usage"],
        )
        displays = {
            value.display_value
            for group in presentation.specification_attributes
            for value in group.values
        }
        self.assertIn("Ceramic", displays)
        self.assertNotIn("GENERIC-ONLY-NAME", displays)
        self.assertEqual(
            [value.value_code for value in presentation.selected_variant_specifications],
            ["white", "60x60", "glossy"],
        )
        self.assertEqual(
            counts_before,
            (ProductAttributeValue.objects.count(), VariantAttributeValue.objects.count()),
        )
        record = captured.records[0]
        self.assertEqual(record.event, "generic_product_presentation_fallback")
        self.assertEqual(record.product_id, self.product.pk)
        self.assertEqual(record.product_sku, self.product.sku)
        self.assertIn("missing required attribute brand", record.catalog_read_cause)

    @override("en")
    def test_live_views_use_generic_context_but_keep_legacy_selector_filter_and_cart_contracts(self):
        listing = self.client.get(reverse("products:product_list"))
        listed = next(
            item for item in listing.context["page_obj"] if item.pk == self.product.pk
        )
        self.assertEqual(listed.catalog_presentation.source, GENERIC_SOURCE)
        self.assertContains(listing, "Ceramic")

        detail = self.client.get(
            f"{reverse('products:product_detail', args=[self.product.slug])}?variant={self.variants[1].pk}"
        )
        self.assertEqual(detail.context["product_presentation"].source, GENERIC_SOURCE)
        self.assertEqual(detail.context["preselected_variant_id"], self.variants[1].pk)
        self.assertEqual(detail.context["variants_data"][1]["size"], "60x60")
        self.assertEqual(
            detail.context["variants_data"][1]["attributes"],
            {"color": "white", "size": "60x60", "finish": "glossy"},
        )
        self.assertEqual(detail.context["selector_source"], "generic")

        filtered = self.client.get(
            f"{reverse('products:product_list')}?colors=white"
        )
        self.assertContains(filtered, self.product.name)
        self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.variants[1].pk, "quantity": 1},
        )
        self.assertEqual(
            self.client.session["cart"], {f"v_{self.variants[1].pk}": 1}
        )

    def test_production_templates_have_no_tile_attribute_or_first_variant_branches(self):
        root = Path(__file__).resolve().parent
        card = (root / "templates/products/components/product_card.html").read_text(encoding="utf-8")
        specs = (root / "templates/products/components/product_specs.html").read_text(encoding="utf-8")
        for source in (card, specs):
            for forbidden in (
                "product.brand", "product.materials", "product.designs",
                "product.usages", "variants.first", 'code == "brand"',
                'code == "material"', 'product_type == "tile"',
            ):
                self.assertNotIn(forbidden, source)


class FrameworkPresentationTests(TestCase):
    def setUp(self):
        self.mobile = ProductType.objects.create(
            code="mobile", name="Mobile", name_fa="موبایل"
        )
        brand_definition = AttributeDefinition.objects.create(
            code="brand", name="Brand", name_fa="برند", data_type="option"
        )
        screen_definition = AttributeDefinition.objects.create(
            code="screen_size", name="Screen Size", name_fa="اندازه صفحه",
            data_type="decimal", unit="inch",
        )
        self.brand_binding = ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=brand_definition,
            scope="product", sort_order=1, show_on_product_card=True,
            show_in_specifications=True,
        )
        self.screen_binding = ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=screen_definition,
            scope="product", sort_order=2, show_on_product_card=True,
            show_in_specifications=True,
        )
        self.brand_option = AttributeOption.objects.create(
            definition=brand_definition, code="acme", name="Acme", name_fa="اکمه"
        )

    def make_mobile_product(self, index=1):
        product = Product.objects.create(
            name=f"Mobile {index}", slug=f"mobile-present-{index}",
            sku=f"MOBILE-PRESENT-{index}", product_type=self.mobile,
        )
        ProductAttributeValue.objects.create(
            product=product, binding=self.brand_binding, option_value=self.brand_option
        )
        ProductAttributeValue.objects.create(
            product=product, binding=self.screen_binding,
            decimal_value=Decimal("6.700000"),
        )
        return product

    def test_mobile_and_zero_dimension_products_use_the_same_production_path(self):
        mobile_product = self.make_mobile_product()
        prepare_product_card_presentations([mobile_product], language="en")
        mobile_detail = compose_product_detail_presentation(
            mobile_product, selected_variant=None, language="en"
        )
        self.assertEqual(mobile_product.catalog_presentation.source, GENERIC_SOURCE)
        self.assertEqual(
            [group.definition_code for group in mobile_product.catalog_presentation.card_attributes],
            ["brand", "screen_size"],
        )
        self.assertEqual(
            [group.definition_code for group in mobile_detail.specification_attributes],
            ["brand", "screen_size"],
        )

        simple_type = ProductType.objects.create(code="simple", name="Simple")
        simple_product = Product.objects.create(
            name="Simple Product", slug="simple-present", sku="SIMPLE-PRESENT",
            product_type=simple_type,
        )
        base_variant = ProductVariant.objects.create(
            product=simple_product, sku="SIMPLE-PRESENT-BASE",
            combination_signature="__base__", stock=1,
        )
        simple_detail = compose_product_detail_presentation(
            simple_product, selected_variant=base_variant, language="en"
        )
        self.assertEqual(simple_detail.source, GENERIC_SOURCE)
        self.assertEqual(simple_detail.specification_attributes, ())
        self.assertEqual(simple_detail.selected_variant_specifications, ())

    def test_production_card_batch_and_detail_queries_are_fixed_and_templates_query_nothing(self):
        products = [self.make_mobile_product(index) for index in range(1, 5)]
        with CaptureQueriesContext(connection) as one:
            prepare_product_card_presentations(products[:1], language="en")
        with CaptureQueriesContext(connection) as four:
            prepare_product_card_presentations(products, language="en")
        self.assertEqual(len(one), len(four))
        self.assertEqual(len(four), 3)

        detail_product = Product.objects.prefetch_related("categories").get(pk=products[0].pk)
        with CaptureQueriesContext(connection) as detail_queries:
            detail = compose_product_detail_presentation(
                detail_product, selected_variant=None, language="en"
            )
        self.assertLessEqual(len(detail_queries), 7)

        with CaptureQueriesContext(connection) as template_queries:
            render_to_string(
                "products/components/product_card.html",
                {
                    "product": products[0],
                    "card_mode": "listing",
                    "storefront_currency": "USD",
                },
            )
            render_to_string(
                "products/components/product_specs.html",
                {"product_presentation": detail},
            )
        self.assertEqual(len(template_queries), 0)
