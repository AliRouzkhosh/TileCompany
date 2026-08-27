from io import StringIO
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.test import RequestFactory

from .catalog_filter import build_generic_catalog
from .commerce_presentation import prepare_commerce_line_presentations
from .currency import Currency
from .models import (
    AttributeDefinition, AttributeOption, Brand, Product, ProductType,
    ProductVariant, VariantAttributeValue, VariantPrice,
)
from .purchase_snapshot import build_generic_v2_purchase_snapshot


class SeedDemoCatalogCommandTests(TestCase):
    def run_seed(self, *args):
        output = StringIO()
        call_command("seed_demo_catalog", *args, stdout=output)
        return output.getvalue()

    def test_default_dry_run_performs_no_writes(self):
        output = self.run_seed()
        self.assertIn("dry-run", output)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Brand.objects.count(), 0)

    @override_settings(APP_ENV="production")
    def test_production_is_rejected(self):
        with self.assertRaisesMessage(CommandError, "forbidden"):
            self.run_seed("--apply")

    @override_settings(APP_ENV="local_production")
    def test_local_production_requires_postgresql(self):
        with self.assertRaisesMessage(CommandError, "PostgreSQL"):
            self.run_seed("--apply")

    def test_invalid_active_type_blocks_seed(self):
        Product.objects.create(name="Bad", slug="bad-untyped", sku="BAD-UNTYPED", is_active=True)
        with self.assertRaisesMessage(CommandError, "Catalog integrity preflight failed"):
            self.run_seed("--apply")

    def test_apply_is_generic_complete_and_idempotent(self):
        self.run_seed("--apply")
        self.assertEqual(Brand.objects.count(), 10)
        self.assertEqual(Product.objects.count(), 50)
        self.assertFalse(ProductType.objects.filter(code="tile").exists())
        self.assertEqual(set(Product.objects.values_list("product_type__code", flat=True)), {"ceramic-tile", "tile-adhesive", "grout", "installation-additive"})
        self.assertFalse(ProductType.objects.exclude(code="tile").filter(attribute_bindings__definition__code="brand").exists())
        variants = ProductVariant.objects.select_related("product")
        self.assertGreater(variants.count(), 50)
        self.assertFalse(variants.filter(sku__isnull=True).exists())
        self.assertFalse(variants.filter(color__isnull=False).exists())
        self.assertFalse(variants.filter(size__isnull=False).exists())
        self.assertFalse(variants.filter(finish__isnull=False).exists())
        self.assertFalse(VariantAttributeValue.objects.filter(option_value__code="standard").exists())
        self.assertEqual(variants.values("product_id", "combination_signature").count(), variants.values("product_id", "combination_signature").distinct().count())
        self.assertEqual(VariantPrice.objects.count(), variants.count() * 2)
        self.assertEqual(set(VariantPrice.objects.values_list("currency", flat=True)), {"USD", "IRT"})
        self.assertLessEqual(Product.objects.filter(variants__prices__compare_at_amount__isnull=False).distinct().count(), 10)
        self.assertTrue(Product.objects.filter(is_active=True).count() == 50)
        adhesive_category = Product.objects.get(sku__contains="AK5-PASTE").primary_category
        adhesive = Product.objects.get(sku__contains="AK5-PASTE")
        adhesive_variant = adhesive.variants.get()
        result = build_generic_catalog(
            RequestFactory().get("/", {"brand": adhesive.brand.slug}), Currency.USD,
            category=adhesive_category,
        )
        facet_codes = {facet["definition_code"] for facet in result.facets}
        self.assertIn("demo-adhesive-form", facet_codes)
        self.assertNotIn("demo-size", facet_codes)
        self.assertIn(adhesive.pk, result.products.values_list("pk", flat=True))
        tile = Product.objects.get(sku__contains="OCEAN")
        tile_result = build_generic_catalog(RequestFactory().get("/"), Currency.USD, category=tile.primary_category)
        self.assertIn("demo-size", {facet["definition_code"] for facet in tile_result.facets})
        line = type("Line", (), {"product": adhesive, "variant": adhesive_variant, "quantity": 1, "unit_price": Decimal("1"), "subtotal": Decimal("1"), "unit_reference_price": None})()
        self.assertEqual(prepare_commerce_line_presentations([line], currency="USD")[adhesive_variant.pk].selling_unit.code, "bucket")
        self.assertEqual(build_generic_v2_purchase_snapshot(adhesive_variant)["version"], 2)
        self.assertEqual(
            set(Product.objects.filter(product_type__code="ceramic-tile").values_list(
                "attribute_values__option_value__code", flat=True,
            )),
            {"ceramic", "porcelain", "wall", "floor", "both", "bathroom", "kitchen", "commercial", "residential"},
        )
        self.assertEqual(
            Product.objects.filter(product_type__code="tile-adhesive", attribute_values__binding__definition__code="demo-application").count(),
            2,
        )
        porcelain = Product.objects.get(sku="DEMO-EEF-PIETRA-A1")
        self.assertTrue(porcelain.attribute_values.filter(option_value__code="porcelain").exists())
        self.assertGreater(Product.objects.filter(categories__slug="wall-floor-tiles").count(), 0)
        package_restrictions = {
            product_type: set(AttributeOption.objects.filter(applicability_restrictions__binding__product_type__code=product_type, applicability_restrictions__binding__definition__code="demo-package-weight").values_list("code", flat=True))
            for product_type in ("tile-adhesive", "grout", "installation-additive")
        }
        self.assertNotEqual(package_restrictions["tile-adhesive"], package_restrictions["grout"])
        self.assertNotIn("standard", package_restrictions["tile-adhesive"])
        before = (Brand.objects.count(), Product.objects.count(), variants.count(), VariantPrice.objects.count())
        self.run_seed("--apply")
        self.assertEqual(before, (Brand.objects.count(), Product.objects.count(), ProductVariant.objects.count(), VariantPrice.objects.count()))

    def test_apply_replaces_legacy_seed_standard_dimension_values(self):
        self.run_seed("--apply")
        variant = ProductVariant.objects.get(sku="DEMO-MAR-VISION-STD")
        binding = variant.product.product_type.attribute_bindings.get(definition__code="demo-size")
        standard = AttributeOption.objects.create(
            definition=binding.definition, code="standard", name="Standard", is_active=True,
        )
        VariantAttributeValue.objects.filter(variant=variant, binding=binding).update(option_value=standard)
        self.run_seed("--apply")
        variant.refresh_from_db()
        self.assertEqual(variant.attribute_values.get(binding=binding).option_value.code, "unknown")
        self.assertFalse(AttributeOption.objects.get(pk=standard.pk).is_active)
