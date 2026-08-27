from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from .catalog_domain import serialize_combination_signature
from .models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)
from .publication_readiness import (
    PublicationSeverity,
    evaluate_product_publication_readiness,
    evaluate_publication_readiness,
    evaluate_variant_publication_readiness,
)
from .purchase_snapshot import PurchaseSnapshotIntegrityError


class PublicationReadinessTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))

    @classmethod
    def setUpTestData(cls):
        cls.mobile = ProductType.objects.create(code="mobile", name="Mobile")

        def definition(code, name):
            return AttributeDefinition.objects.create(code=code, name=name, data_type="option")

        def binding(definition, scope, order, *, dimension=False, required=True, snapshot=True):
            return ProductTypeAttribute.objects.create(
                product_type=cls.mobile, definition=definition, scope=scope,
                is_required=required, is_variant_dimension=dimension,
                include_in_purchase_snapshot=snapshot, sort_order=order,
            )

        cls.brand_def = definition("brand", "Brand")
        cls.color_def = definition("color", "Color")
        cls.ram_def = definition("ram", "RAM")
        cls.storage_def = definition("storage", "Storage")
        cls.brand_binding = binding(cls.brand_def, "product", 10)
        cls.color_binding = binding(cls.color_def, "variant", 10, dimension=True)
        cls.ram_binding = binding(cls.ram_def, "variant", 20, dimension=True)
        cls.storage_binding = binding(cls.storage_def, "variant", 30, dimension=True)
        cls.apple = AttributeOption.objects.create(definition=cls.brand_def, code="apple", name="Apple")
        cls.black = AttributeOption.objects.create(definition=cls.color_def, code="black", name="Black")
        cls.white = AttributeOption.objects.create(definition=cls.color_def, code="white", name="White")
        cls.ram12 = AttributeOption.objects.create(definition=cls.ram_def, code="12gb", name="12 GB")
        cls.storage256 = AttributeOption.objects.create(definition=cls.storage_def, code="256gb", name="256 GB")
        cls.product = Product.objects.create(
            product_type=cls.mobile, name="iPhone 17 Pro", slug="iphone-readiness",
            sku="IPHONE-READY", is_active=False,
        )
        ProductAttributeValue.objects.create(product=cls.product, binding=cls.brand_binding, option_value=cls.apple)
        cls.variant = ProductVariant.objects.create(
            product=cls.product, sku="IPHONE-BLK-12-256", price_unit="unit",
            stock=10, low_stock_threshold=2, is_active=False,
            combination_signature=serialize_combination_signature((
                ("color", "black"), ("ram", "12gb"), ("storage", "256gb"),
            )),
        )
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.color_binding, option_value=cls.black)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.ram_binding, option_value=cls.ram12)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.storage_binding, option_value=cls.storage256)
        VariantPrice.objects.create(variant=cls.variant, currency="USD", amount=Decimal("999.99"))
        VariantPrice.objects.create(variant=cls.variant, currency="IRT", amount=Decimal("99900000"))

    def issue_codes(self, result):
        return {issue.code for issue in result.issues}

    def test_ready_inactive_mobile_variant_and_product_lifecycle_dependency(self):
        variant = evaluate_variant_publication_readiness(self.variant)
        product = evaluate_product_publication_readiness(self.product)
        self.assertTrue(variant.ready)
        self.assertFalse(variant.is_active)
        self.assertEqual(variant.blockers, ())
        self.assertEqual(variant.required_currencies, ("USD", "IRT"))
        self.assertEqual(variant.available_currencies, ("IRT", "USD"))
        self.assertFalse(product.ready)
        self.assertEqual(product.ready_variant_count, 1)
        self.assertEqual(product.active_ready_variant_count, 0)
        self.assertEqual(self.issue_codes(product), {"no_active_publication_ready_variant"})

    def test_missing_currency_blocks_then_restore_returns_ready(self):
        self.variant.prices.filter(currency="IRT").delete()
        blocked = evaluate_variant_publication_readiness(self.variant)
        self.assertFalse(blocked.ready)
        issue = next(issue for issue in blocked.blockers if issue.code == "missing_currency_price")
        self.assertEqual(issue.related_identity, "IRT")
        VariantPrice.objects.create(variant=self.variant, currency="IRT", amount=Decimal("99900000"))
        self.assertTrue(evaluate_variant_publication_readiness(self.variant).ready)

    def test_missing_required_product_attribute_blocks_product_and_variant(self):
        self.product.attribute_values.filter(binding=self.brand_binding).delete()
        product = evaluate_product_publication_readiness(self.product)
        variant = next(iter(product.variant_results))
        self.assertIn("missing_required_product_attribute", self.issue_codes(product))
        self.assertIn("product_catalog_not_ready", self.issue_codes(variant))

    def test_boolean_false_is_a_real_required_value(self):
        definition = AttributeDefinition.objects.create(code="refurbished", name="Refurbished", data_type="boolean")
        binding = ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=definition, scope="product",
            is_required=True, include_in_purchase_snapshot=True, sort_order=50,
        )
        ProductAttributeValue.objects.create(product=self.product, binding=binding, boolean_value=False)
        result = evaluate_variant_publication_readiness(self.variant)
        self.assertTrue(result.ready)
        self.assertNotIn("missing_required_product_attribute", self.issue_codes(result))

    def test_required_currency_policy_is_injectable(self):
        self.variant.prices.filter(currency="IRT").delete()
        result = evaluate_variant_publication_readiness(self.variant, required_currencies=("USD",))
        self.assertTrue(result.ready)
        self.assertEqual(result.required_currencies, ("USD",))

    def test_zero_stock_is_warning_not_blocker(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=0)
        result = evaluate_variant_publication_readiness(self.variant)
        self.assertTrue(result.ready)
        self.assertEqual([issue.code for issue in result.warnings], ["zero_stock"])
        self.assertEqual(result.warnings[0].severity, PublicationSeverity.WARNING)

    def test_fractional_toman_price_is_blocking(self):
        self.variant.prices.filter(currency="IRT").update(amount=Decimal("99900000.50"))
        result = evaluate_variant_publication_readiness(self.variant)
        self.assertIn("invalid_currency_price", self.issue_codes(result))
        self.assertFalse(result.ready)

    def test_retired_and_disallowed_options_block_without_mutation(self):
        self.black.is_active = False
        self.black.save(update_fields=["is_active"])
        retired = evaluate_variant_publication_readiness(self.variant)
        self.assertIn("retired_option", self.issue_codes(retired))
        self.black.is_active = True
        self.black.save(update_fields=["is_active"])
        ProductTypeAttributeOption.objects.create(binding=self.color_binding, option=self.white)
        disallowed = evaluate_variant_publication_readiness(self.variant)
        self.assertIn("disallowed_option", self.issue_codes(disallowed))
        self.assertEqual(self.variant.attribute_values.get(binding=self.color_binding).option_value_id, self.black.pk)
        self.assertFalse(self.variant.is_active)

    def test_missing_dimension_and_signature_corruption_block_without_repair(self):
        value = self.variant.attribute_values.get(binding=self.storage_binding)
        value.delete()
        missing = evaluate_variant_publication_readiness(self.variant)
        self.assertIn("missing_required_variant_dimension", self.issue_codes(missing))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.combination_signature, "color=black|ram=12gb|storage=256gb")

        VariantAttributeValue.objects.create(variant=self.variant, binding=self.storage_binding, option_value=self.storage256)
        ProductVariant.objects.filter(pk=self.variant.pk).update(combination_signature="corrupt")
        corrupt = evaluate_variant_publication_readiness(self.variant)
        self.assertIn("invalid_combination_signature", self.issue_codes(corrupt))
        self.assertEqual(ProductVariant.objects.get(pk=self.variant.pk).combination_signature, "corrupt")

    def test_snapshot_builder_failure_is_a_stable_blocker(self):
        with patch("products.publication_readiness.prepare_purchase_snapshots", side_effect=PurchaseSnapshotIntegrityError("broken snapshot")):
            result = evaluate_variant_publication_readiness(self.variant)
        self.assertIn("snapshot_readiness_failed", self.issue_codes(result))
        self.assertFalse(result.ready)

    def test_primary_category_must_be_assigned_but_is_not_required(self):
        self.assertNotIn("primary_category_not_assigned", self.issue_codes(evaluate_product_publication_readiness(self.product)))
        category = Category.objects.create(name="Phones", slug="phones-ready")
        Product.objects.filter(pk=self.product.pk).update(primary_category=category)
        result = evaluate_product_publication_readiness(self.product)
        self.assertIn("primary_category_not_assigned", self.issue_codes(result))

    def test_zero_dimension_base_variant_is_ready(self):
        simple = ProductType.objects.create(code="simple", name="Simple")
        product = Product.objects.create(product_type=simple, name="Gift Card", slug="gift-card-ready", sku="GIFT", is_active=False)
        variant = ProductVariant.objects.create(product=product, sku="GIFT-BASE", price_unit="unit", combination_signature="__base__", stock=1, is_active=False)
        VariantPrice.objects.create(variant=variant, currency="USD", amount=Decimal("10"))
        VariantPrice.objects.create(variant=variant, currency="IRT", amount=Decimal("500000"))
        result = evaluate_variant_publication_readiness(variant)
        self.assertTrue(result.ready)
        self.assertEqual(result.blockers, ())

    def test_tile_compatibility_uses_legacy_snapshot_capability(self):
        tile = ProductType.objects.create(code="tile", name="Tile")
        product = Product.objects.create(product_type=tile, name="Tile", slug="tile-ready", sku="TILE-READY", is_active=False)
        variant = ProductVariant.objects.create(product=product, sku="TILE-BASE", price_unit="box", combination_signature="__base__", stock=1, is_active=False)
        VariantPrice.objects.create(variant=variant, currency="USD", amount=Decimal("10"))
        VariantPrice.objects.create(variant=variant, currency="IRT", amount=Decimal("500000"))
        self.assertTrue(evaluate_variant_publication_readiness(variant).ready)

    def test_product_counts_ready_zero_stock_and_incomplete_draft_variants(self):
        second = ProductVariant.objects.create(
            product=self.product, sku="IPHONE-WHT-12-256", price_unit="unit", stock=0,
            is_active=False, combination_signature="color=white|ram=12gb|storage=256gb",
        )
        VariantAttributeValue.objects.create(variant=second, binding=self.color_binding, option_value=self.white)
        VariantAttributeValue.objects.create(variant=second, binding=self.ram_binding, option_value=self.ram12)
        VariantAttributeValue.objects.create(variant=second, binding=self.storage_binding, option_value=self.storage256)
        VariantPrice.objects.create(variant=second, currency="USD", amount=Decimal("999"))
        VariantPrice.objects.create(variant=second, currency="IRT", amount=Decimal("99900000"))
        ProductVariant.objects.create(product=self.product, sku="INCOMPLETE", price_unit="unit", stock=1, is_active=False)
        result = evaluate_product_publication_readiness(self.product)
        self.assertEqual(result.ready_variant_count, 2)
        self.assertEqual(result.active_ready_variant_count, 0)
        self.assertIn("incomplete_draft_variants", self.issue_codes(result))

    def test_active_ready_variant_satisfies_product_lifecycle_rule(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(is_active=True)
        result = evaluate_product_publication_readiness(self.product)
        self.assertTrue(result.ready)
        self.assertEqual(result.active_ready_variant_count, 1)

    def test_batch_queries_are_bounded_and_evaluation_writes_nothing(self):
        before = (self.product.is_active, self.variant.is_active)
        with self.assertNumQueries(20):
            one = evaluate_publication_readiness([self.product])
        sibling = ProductVariant.objects.create(
            product=self.product, sku="QUERY-SIBLING", price_unit="unit", stock=1,
            is_active=False, combination_signature="color=white|ram=12gb|storage=256gb",
        )
        VariantAttributeValue.objects.create(variant=sibling, binding=self.color_binding, option_value=self.white)
        VariantAttributeValue.objects.create(variant=sibling, binding=self.ram_binding, option_value=self.ram12)
        VariantAttributeValue.objects.create(variant=sibling, binding=self.storage_binding, option_value=self.storage256)
        VariantPrice.objects.create(variant=sibling, currency="USD", amount=Decimal("999"))
        VariantPrice.objects.create(variant=sibling, currency="IRT", amount=Decimal("99900000"))
        with self.assertNumQueries(20):
            several_variants = evaluate_publication_readiness([self.product])
        other = Product.objects.create(product_type=self.mobile, name="Second", slug="second-ready", sku="SECOND", is_active=False)
        ProductAttributeValue.objects.create(product=other, binding=self.brand_binding, option_value=self.apple)
        with self.assertNumQueries(20):
            many = evaluate_publication_readiness([self.product, other])
        self.assertEqual(len(one.product_results), 1)
        self.assertEqual(len(several_variants.variant_results), 2)
        self.assertEqual(len(many.product_results), 2)
        self.product.refresh_from_db(); self.variant.refresh_from_db()
        self.assertEqual((self.product.is_active, self.variant.is_active), before)

    def test_dashboard_panels_and_controls_do_not_mutate_on_get(self):
        call_command("seed_dashboard_roles", verbosity=0)
        staff = get_user_model().objects.create_user("readiness-staff", password="x")
        staff.groups.add(Group.objects.get(name="Product Manager"))
        self.client.force_login(staff)
        before = (self.product.is_active, self.variant.is_active)
        product_response = self.client.get(reverse("dashboard:products:generic_detail", args=[self.product.pk]))
        variant_response = self.client.get(reverse("dashboard:products:generic_variant_edit", args=[self.product.pk, self.variant.pk]))
        self.assertContains(product_response, "Publication readiness")
        self.assertContains(product_response, "no_active_publication_ready_variant", html=False)
        self.assertContains(variant_response, "Publication readiness")
        self.assertContains(product_response, "Publish Product")
        self.assertContains(variant_response, "Publish Variant")
        self.product.refresh_from_db(); self.variant.refresh_from_db()
        self.assertEqual((self.product.is_active, self.variant.is_active), before)

    def test_readiness_component_renders_with_zero_queries(self):
        readiness = evaluate_product_publication_readiness(self.product)
        with self.assertNumQueries(0):
            rendered = render_to_string("dashboard/components/publication_readiness.html", {"readiness": readiness})
        self.assertIn("Publication readiness", rendered)

    def test_audit_command_classifies_drafts_and_performs_zero_writes(self):
        output = StringIO()
        before = (self.product.is_active, self.variant.is_active)
        call_command("audit_catalog_publication_readiness", stdout=output)
        rendered = output.getvalue()
        self.assertIn("DRAFT READY Variants: 1", rendered)
        self.assertIn("DRAFT BLOCKED Products: 1", rendered)
        self.product.refresh_from_db(); self.variant.refresh_from_db()
        self.assertEqual((self.product.is_active, self.variant.is_active), before)

    def test_audit_command_returns_error_for_active_invalid_product(self):
        Product.objects.filter(pk=self.product.pk).update(is_active=True)
        ProductVariant.objects.filter(pk=self.variant.pk).update(is_active=False)
        with self.assertRaises(CommandError):
            call_command("audit_catalog_publication_readiness", stdout=StringIO())
