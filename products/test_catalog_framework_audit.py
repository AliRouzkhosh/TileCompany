from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from products.catalog_framework_audit import run_catalog_framework_audit
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
)


class CatalogFrameworkAuditTests(TestCase):
    def setUp(self):
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")
        self.color = AttributeDefinition.objects.create(code="color", name="Color", data_type="option")
        self.black = AttributeOption.objects.create(definition=self.color, code="black", name="Black")
        self.dimension = ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=self.color, scope="variant", cardinality="single",
            is_required=True, is_variant_dimension=True, include_in_purchase_snapshot=True,
        )
        ProductTypeAttributeOption.objects.create(binding=self.dimension, option=self.black)
        self.product = Product.objects.create(product_type=self.mobile, name="Phone", slug="phone", sku="PHONE")

    def codes(self, report, level="BLOCKING"):
        return {issue.code for issue in report.issues if issue.level == level}

    def make_valid_variant(self):
        variant = ProductVariant.objects.create(product=self.product, sku="PHONE-BLACK", is_active=False)
        VariantAttributeValue.objects.create(variant=variant, binding=self.dimension, option_value=self.black)
        variant.combination_signature = "color=black"
        variant.is_active = True
        variant.save(update_fields=["combination_signature", "is_active"])
        return variant

    def test_valid_synthetic_schema_passes(self):
        self.make_valid_variant()
        self.assertFalse(run_catalog_framework_audit().blocking_issues)
        output = StringIO()
        call_command("audit_catalog_framework", stdout=output)
        self.assertIn("CATALOG FRAMEWORK USABLE", output.getvalue())

    def test_malformed_product_and_variant_values_are_detected(self):
        brand = AttributeDefinition.objects.create(code="brand", name="Brand", data_type="option")
        apple = AttributeOption.objects.create(definition=brand, code="apple", name="Apple")
        other = ProductType.objects.create(code="other", name="Other")
        other_product_binding = ProductTypeAttribute.objects.create(product_type=other, definition=brand, scope="product")
        ProductAttributeValue.objects.create(product=self.product, binding=other_product_binding, option_value=apple)
        other_variant_binding = ProductTypeAttribute.objects.create(product_type=other, definition=self.color, scope="variant")
        variant = ProductVariant.objects.create(product=self.product, sku="BAD", is_active=False)
        VariantAttributeValue.objects.create(variant=variant, binding=other_variant_binding, option_value=self.black)
        codes = self.codes(run_catalog_framework_audit())
        self.assertIn("invalid_product_value", codes)
        self.assertIn("invalid_variant_value", codes)

    def test_missing_dimension_and_signature_mismatch_are_detected(self):
        ProductVariant.objects.create(product=self.product, sku="MISSING", is_active=True)
        report = run_catalog_framework_audit()
        self.assertIn("missing_required_dimension", self.codes(report))
        variant = self.make_valid_variant()
        variant.combination_signature = "color=wrong"
        variant.save(update_fields=["combination_signature"])
        self.assertIn("signature_mismatch", self.codes(run_catalog_framework_audit()))

    def test_retired_stored_option_is_warning_not_corruption(self):
        self.make_valid_variant()
        self.black.is_active = False
        self.black.save(update_fields=["is_active"])
        report = run_catalog_framework_audit()
        self.assertFalse(report.blocking_issues)
        self.assertIn("retired_or_disallowed_stored_value", self.codes(report, "WARNING"))
