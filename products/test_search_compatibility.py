from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from products.catalog_read import CatalogReadIntegrityError
from products.currency import Currency
from products.models import (
    AttributeDefinition, Brand, Product, ProductAttributeValue, ProductType,
    ProductTypeAttribute, ProductVariant,
)
from products.search.autocomplete import autocomplete
from products.search.engine import GENERIC_CORE_FIELDS, search_products
from products.search.synonyms import SYNONYMS


class SearchCompatibilityBoundaryTests(TestCase):
    def setUp(self):
        self.tile = ProductType.objects.create(code="tile", name="Tile")
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")

    def product(self, product_type, sku, *, name=None, series=""):
        product = Product.objects.create(
            product_type=product_type,
            name=name or sku,
            slug=sku.lower(),
            sku=sku,
            series_name=series,
            is_active=True,
        )
        ProductVariant.objects.create(
            product=product, sku=f"{sku}-V", price_unit="unit", is_active=True,
        )
        return product

    def test_current_tile_generic_failure_uses_complete_tile_fallback_only(self):
        tile_product = self.product(
            self.tile, "TILE-SERIES", name="Tile Product", series="Heritage",
        )
        # A non-Tile legacy column containing the same text must never be mixed
        # into the degraded Tile result set.
        self.product(
            self.mobile, "MOBILE-SERIES", name="Mobile Product", series="Heritage",
        )
        with patch(
            "products.search.engine._generic_search",
            side_effect=CatalogReadIntegrityError(["broken generic state"]),
        ), self.assertLogs("products.search.engine", level="ERROR"):
            results = list(search_products("Heritage"))
        self.assertEqual([product.pk for product in results], [tile_product.pk])
        self.assertEqual(results[0].search_source, "legacy_tile")

    def test_current_tile_autocomplete_failure_is_wholly_legacy(self):
        brand = Brand.objects.create(name="Ariana", slug="ariana")
        tile_product = self.product(self.tile, "ARIANA-TILE", name="Ariana Tile")
        tile_product.brand = brand
        tile_product.save(update_fields=["brand"])
        with patch(
            "products.search.engine._generic_search",
            side_effect=CatalogReadIntegrityError(["broken generic suggestions"]),
        ), self.assertLogs("products.search.autocomplete", level="ERROR"):
            payload = autocomplete("Ariana", Currency.USD)
        self.assertEqual([item["name"] for item in payload["products"]], ["Ariana Tile"])
        self.assertEqual(payload["groups"][0]["items"][0]["value"], "ariana")
        self.assertTrue(all(
            item.get("type") != "attribute_option"
            for group in payload["groups"] for item in group["items"]
        ))

    def test_null_product_type_legacy_fallback_remains_supported(self):
        Product.objects.all().delete()
        product = self.product(None, "LEGACY", name="Legacy", series="Archive")
        with self.assertLogs("products.search.engine", level="ERROR"):
            result = search_products("Archive").get()
        self.assertEqual((result.pk, result.search_source), (product.pk, "legacy_tile"))

    def test_non_tile_generic_failure_fails_closed_for_search_and_autocomplete(self):
        Product.objects.filter(product_type=self.tile).delete()
        self.product(self.mobile, "PHONE", name="Phone")
        with patch(
            "products.search.engine._generic_search",
            side_effect=CatalogReadIntegrityError(["broken"]),
        ), self.assertLogs("products.search.engine", level="ERROR"):
            self.assertFalse(search_products("Phone").exists())
        with patch(
            "products.search.autocomplete.generic_grouped_suggestions",
            side_effect=CatalogReadIntegrityError(["broken"]),
        ), self.assertLogs("products.search.autocomplete", level="ERROR"):
            self.assertEqual(
                autocomplete("Phone", Currency.USD),
                {"groups": [], "products": []},
            )

    def test_series_is_tile_compatibility_only_and_generic_attribute_is_searchable(self):
        tile_product = self.product(
            self.tile, "TILE", name="Tile Product", series="Legacy Collection",
        )
        mobile_product = self.product(
            self.mobile, "PHONE", name="Phone", series="False Generic Series",
        )
        self.assertEqual(search_products("Legacy Collection").get(), tile_product)
        self.assertNotIn(mobile_product, search_products("False Generic Series"))

        definition = AttributeDefinition.objects.create(
            code="series", name="Series", data_type="text",
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=self.mobile,
            definition=definition,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            is_searchable=True,
        )
        ProductAttributeValue.objects.create(
            product=mobile_product, binding=binding, text_value="Galaxy Family",
        )
        self.assertEqual(search_products("Galaxy Family").get(), mobile_product)

    def test_tile_synonyms_do_not_expand_non_tile_generic_search(self):
        product = self.product(self.mobile, "OUTDOOR", name="Outdoor Phone")
        self.assertNotIn(product, search_products("exterior"))
        self.assertEqual(search_products("outdoor").get(), product)

    def test_synonym_map_is_explicitly_tile_compatibility_only(self):
        self.assertTrue(SYNONYMS)
        search_dir = Path(__file__).resolve().parent / "search"
        importing_files = {
            path.name
            for path in search_dir.glob("*.py")
            if "import SYNONYMS" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(importing_files, {"tile_compat.py"})

    def test_generic_core_and_source_have_no_domain_attribute_codes(self):
        self.assertNotIn("series_name", GENERIC_CORE_FIELDS)
        self.assertNotIn("series_name_fa", GENERIC_CORE_FIELDS)
        engine_source = (
            Path(__file__).resolve().parent / "search" / "engine.py"
        ).read_text(encoding="utf-8")
        for code in (
            "brand", "material", "design", "usage", "color", "size",
            "finish", "ram", "storage", "ssd", "cpu", "gpu", "mobile",
            "laptop",
        ):
            self.assertNotIn(f'"{code}"', engine_source)
