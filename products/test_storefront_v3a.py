from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from products.catalog_filter import build_generic_catalog, build_generic_filter_state
from products.catalog_presentation import prepare_product_card_presentations
from products.currency import Currency
from products.models import (
    AttributeDefinition, AttributeOption, Category, Product,
    ProductAttributeValue, ProductType, ProductTypeAttribute,
    ProductVariant, VariantPrice,
)
from products.selectors import get_featured_products


class StorefrontV3AMobileCatalogTests(TestCase):
    def source(self, path):
        return (Path(settings.BASE_DIR) / path).read_text(encoding="utf-8")

    def setUp(self):
        self.product_type = ProductType.objects.create(code="device", name="Device")
        self.category = Category.objects.create(
            name="Portable Products", name_fa="محصولات قابل حمل", slug="portable-products"
        )
        self.product = Product.objects.create(
            product_type=self.product_type,
            name="Generic Portable Product",
            name_fa="محصول قابل حمل عمومی",
            slug="generic-portable-product",
            sku="GENERIC-PORTABLE",
            is_active=True,
            is_featured=True,
        )
        self.product.categories.add(self.category)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="GENERIC-PORTABLE-A",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=4,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=self.variant,
            currency=Currency.USD,
            amount=Decimal("120.00"),
            compare_at_amount=Decimal("150.00"),
        )
        for index, (code, label, option_code, option_label) in enumerate((
            ("platform", "Platform", "portable", "Portable"),
            ("generation", "Generation", "current", "Current"),
            ("audience", "Audience", "general", "General"),
        )):
            definition = AttributeDefinition.objects.create(
                code=code, name=label, data_type=AttributeDefinition.DataType.OPTION
            )
            option = AttributeOption.objects.create(
                definition=definition, code=option_code, name=option_label,
                name_fa=f"ویژگی {index + 1}",
            )
            binding = ProductTypeAttribute.objects.create(
                product_type=self.product_type,
                definition=definition,
                scope=ProductTypeAttribute.Scope.PRODUCT,
                show_on_product_card=True,
                show_on_storefront=True,
                is_filterable=True,
                sort_order=index,
            )
            ProductAttributeValue.objects.create(
                product=self.product, binding=binding, option_value=option
            )

    def prepared(self, language="en"):
        products = list(get_featured_products(Currency.USD))
        prepare_product_card_presentations(products, language=language, currency=Currency.USD)
        products[0].target_variant_id = self.variant.pk
        return products[0]

    def render_mobile(self, *, language="en", image=""):
        self.product.image = image
        self.product.save(update_fields=["image"])
        product = self.prepared(language)
        with translation.override(language):
            with self.assertNumQueries(0):
                return render_to_string(
                    "products/components/product_card_mobile.html",
                    {
                        "product": product,
                        "storefront_currency": Currency.USD,
                        "query_string": "sort=price_asc",
                    },
                )

    def test_mobile_row_uses_shared_prepared_contract_and_target_variant_url(self):
        rendered = self.render_mobile()
        self.assertIn("Generic Portable Product", rendered)
        self.assertIn("Portable", rendered)
        self.assertIn("Current", rendered)
        self.assertNotIn("General", rendered)
        self.assertIn("$120.00", rendered)
        self.assertIn("$150.00", rendered)
        self.assertIn("Per Unit", rendered)
        self.assertIn(f'variant={self.variant.pk}" hx-boost="false"', rendered)
        self.assertNotIn("sort=price_asc", rendered)
        self.assertEqual(rendered.count("<a "), 1)
        self.assertNotIn("<button", rendered)
        self.assertNotIn("<form", rendered)

    def test_mobile_row_localizes_name_and_limits_generic_attributes_to_two(self):
        rendered = self.render_mobile(language="fa")
        self.assertIn("محصول قابل حمل عمومی", rendered)
        self.assertIn("ویژگی 1", rendered)
        self.assertIn("ویژگی 2", rendered)
        self.assertNotIn("ویژگی 3", rendered)

    def test_mobile_media_image_and_fallback_share_square_geometry(self):
        image = self.render_mobile(image="products/device.jpg")
        fallback = self.render_mobile(image="")
        geometry = "relative aspect-square w-full self-center"
        self.assertIn(geometry, image)
        self.assertIn(geometry, fallback)
        self.assertIn("data-mobile-product-image-fallback", fallback)
        self.assertNotIn("data-mobile-product-image-fallback", image)

    def test_catalog_renders_phone_rows_and_preserves_desktop_discovery_cards(self):
        response = self.client.get("/en/shop/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-mobile-product-results")
        self.assertContains(response, "data-mobile-product-row")
        self.assertContains(response, "data-desktop-product-results")
        self.assertContains(response, "product-discovery-card")
        self.assertContains(response, "md:hidden")
        self.assertContains(response, "hidden gap-6 md:grid md:grid-cols-2 lg:grid-cols-3")

    def test_category_and_search_reuse_the_same_result_component(self):
        category = self.client.get(f"/en/shop/category/{self.category.slug}/")
        search = self.client.get("/en/shop/?q=Generic")
        for response in (category, search):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "data-mobile-product-row")
            self.assertContains(response, "data-desktop-product-results")

    def test_toolbar_counts_only_meaningful_filters_and_keeps_removal_urls(self):
        response = self.client.get("/en/shop/", {
            "q": "Generic", "sort": "price_asc", "attr_platform": "portable",
            "min_price": "100", "in_stock": "1", "page": "2",
        })
        self.assertEqual(response.context["active_filter_count"], 3)
        self.assertContains(response, "data-mobile-sort")
        self.assertContains(response, "data-mobile-filter-trigger")
        platform = next(
            chip for chip in response.context["active_filter_chips"]
            if chip["display_value"] == "Portable"
        )
        removed = parse_qs(urlsplit(platform["remove_url"]).query)
        self.assertEqual(removed["q"], ["Generic"])
        self.assertEqual(removed["sort"], ["price_asc"])
        self.assertEqual(removed["min_price"], ["100"])
        self.assertEqual(removed["in_stock"], ["1"])
        self.assertNotIn("attr_platform", removed)
        self.assertNotIn("page", removed)

    def test_v3a_sources_are_domain_neutral_and_contain_no_template_orm_reads(self):
        mobile = self.source("products/templates/products/components/product_card_mobile.html")
        toolbar = self.source("products/templates/products/components/shop_toolbar.html")
        combined = mobile + toolbar
        for forbidden in (
            ">Material<", ">Finish<", ">Color<", ">Size<", ">Brand<", "ProductType",
            ".variants", ".prices", ".objects", "length_mm", "width_mm",
        ):
            self.assertNotIn(forbidden, combined)

    def test_catalog_query_count_does_not_scale_with_result_rows(self):
        def measure(extra):
            for index in range(extra):
                product = Product.objects.create(
                    product_type=self.product_type,
                    name=f"Extra {index}", slug=f"extra-{extra}-{index}",
                    sku=f"EXTRA-{extra}-{index}", is_active=True,
                )
                variant = ProductVariant.objects.create(
                    product=product, sku=f"EXTRA-{extra}-{index}-A",
                    price_unit=ProductVariant.PriceUnit.UNIT, stock=1, is_active=True,
                )
                VariantPrice.objects.create(
                    variant=variant, currency=Currency.USD, amount=Decimal("20")
                )
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get("/en/shop/")
                self.assertEqual(response.status_code, 200)
            return len(queries)

        baseline = measure(0)
        scaled = measure(3)
        self.assertEqual(baseline, scaled)
