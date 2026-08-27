from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from .catalog_backfill import (
    SPEC_BY_CODE,
    TILE_PRODUCT_TYPE,
    TileReconciliationBlocked,
    apply_tile_catalog,
    capture_preservation_baseline,
    inspect_tile_catalog,
)
from .catalog_domain import calculate_variant_combination_signature
from .catalog_domain import available_options_for_binding
from .catalog_read_audit import run_generic_catalog_read_audit
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
    ProductTypeAttributeOption,
    ProductVariant,
    Size,
    Usage,
    VariantAttributeValue,
    VariantPrice,
)


class TileBackfillTestCase(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Atlas", name_fa="اطلس", slug="atlas"
        )
        self.other_brand = Brand.objects.create(
            name="Nova", name_fa="نوا", slug="nova"
        )
        self.designs = [
            Design.objects.create(name="Stone", name_fa="سنگ", slug="stone"),
            Design.objects.create(name="Plain", name_fa="ساده", slug="plain"),
        ]
        self.materials = [
            Material.objects.create(name="Ceramic", name_fa="سرامیک", slug="ceramic"),
            Material.objects.create(name="Glass", name_fa="شیشه", slug="glass"),
        ]
        self.usages = [
            Usage.objects.create(name="Floor", name_fa="کف", slug="floor"),
            Usage.objects.create(name="Wall", name_fa="دیوار", slug="wall"),
        ]
        self.color = Color.objects.create(
            name="Black", name_fa="مشکی", slug="black"
        )
        self.other_color = Color.objects.create(
            name="White", name_fa="سفید", slug="white"
        )
        self.size = Size.objects.create(
            name="60x60", name_fa="۶۰×۶۰", slug="60x60", length_mm=600, width_mm=600
        )
        self.finish = Finish.objects.create(
            name="Matte", name_fa="مات", slug="matte"
        )
        self.other_finish = Finish.objects.create(
            name="Gloss", name_fa="براق", slug="gloss"
        )
        self.category = Category.objects.create(name="Floor", slug="floor-category")
        self.product = Product.objects.create(
            name="Tile", slug="tile", sku="P-TILE", brand=self.brand
        )
        self.product.categories.add(self.category)
        self.product.designs.set(self.designs)
        self.product.materials.set(self.materials)
        self.product.usages.set(self.usages)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="V-TILE",
            color=self.color,
            size=self.size,
            finish=self.finish,
            length_mm=600,
            width_mm=600,
            thickness_mm="9.50",
            tiles_per_box=4,
            coverage_per_box_m2="1.440",
            stock=17,
            low_stock_threshold=3,
            sort_order=4,
        )
        VariantPrice.objects.create(
            variant=self.variant, currency="USD", amount="20.00"
        )

    def apply(self):
        return apply_tile_catalog()

    def test_dry_run_command_performs_zero_writes(self):
        before = capture_preservation_baseline()
        output = StringIO()
        call_command("backfill_tile_catalog_framework", "--dry-run", stdout=output)
        self.assertIn("zero writes performed", output.getvalue())
        self.assertEqual(ProductType.objects.count(), 0)
        self.assertEqual(AttributeDefinition.objects.count(), 0)
        self.assertEqual(capture_preservation_baseline(), before)

    def test_schema_creation_has_exact_codes_configuration_and_explicit_memberships(self):
        self.apply()
        tile = ProductType.objects.get(code="tile")
        self.assertEqual(tile.name, TILE_PRODUCT_TYPE["name"])
        self.assertEqual(tile.name_fa, "کاشی")
        self.assertEqual(
            set(AttributeDefinition.objects.values_list("code", flat=True)),
            set(SPEC_BY_CODE),
        )
        self.assertEqual(tile.attribute_bindings.count(), 7)
        self.assertEqual(
            ProductTypeAttributeOption.objects.count(),
            AttributeOption.objects.count(),
        )
        for code, spec in SPEC_BY_CODE.items():
            binding = tile.attribute_bindings.get(definition__code=code)
            for field, expected in spec.binding_values.items():
                self.assertEqual(getattr(binding, field), expected, f"{code}.{field}")
            memberships = list(
                binding.option_restrictions.select_related("option").order_by("sort_order", "id")
            )
            self.assertEqual(
                [(row.option.code, row.sort_order, row.is_active) for row in memberships],
                [(option.code, option.sort_order, True) for option in binding.definition.options.all()],
            )

    def test_options_copy_legacy_slug_names_and_definition_scope(self):
        self.apply()
        option = AttributeOption.objects.get(definition__code="brand", code="atlas")
        self.assertEqual((option.name, option.name_fa), ("Atlas", "اطلس"))
        self.assertFalse(
            AttributeOption.objects.filter(definition__code="color", code="atlas").exists()
        )

    def test_product_values_preserve_single_and_multiple_legacy_values(self):
        self.apply()
        self.product.refresh_from_db()
        self.assertEqual(self.product.product_type.code, "tile")
        actual = {
            code: set(
                self.product.attribute_values.filter(binding__definition__code=code)
                .values_list("option_value__code", flat=True)
            )
            for code in ("brand", "design", "material", "usage")
        }
        self.assertEqual(actual["brand"], {"atlas"})
        self.assertEqual(actual["design"], {"stone", "plain"})
        self.assertEqual(actual["material"], {"ceramic", "glass"})
        self.assertEqual(actual["usage"], {"floor", "wall"})

    def test_missing_optional_product_values_create_no_fake_values(self):
        empty = Product.objects.create(name="Empty", slug="empty", sku="P-EMPTY")
        self.apply()
        empty.refresh_from_db()
        self.assertEqual(empty.product_type.code, "tile")
        self.assertFalse(empty.attribute_values.exists())

    def test_variant_values_and_signature_mirror_legacy_dimensions_only(self):
        self.apply()
        actual = {
            value.binding.definition.code: value.option_value.code
            for value in self.variant.attribute_values.select_related(
                "binding__definition", "option_value"
            )
        }
        self.assertEqual(
            actual, {"color": "black", "size": "60x60", "finish": "matte"}
        )
        self.variant.refresh_from_db()
        self.assertEqual(
            self.variant.combination_signature,
            "color=black|finish=matte|size=60x60",
        )
        self.assertFalse(
            AttributeDefinition.objects.filter(
                code__in=("length", "width", "thickness", "tiles_per_box", "coverage_per_box", "price_unit")
            ).exists()
        )

    def test_signature_is_independent_of_labels_and_display_order(self):
        self.apply()
        self.variant.refresh_from_db()
        original = calculate_variant_combination_signature(self.variant)
        AttributeOption.objects.filter(definition__code="color", code="black").update(
            name="Renamed", name_fa="تغییر", sort_order=999
        )
        ProductTypeAttribute.objects.filter(definition__code="color").update(sort_order=999)
        self.assertEqual(calculate_variant_combination_signature(self.variant), original)

    def test_duplicate_legacy_combination_blocks_before_apply(self):
        ProductVariant.objects.create(
            product=self.product,
            sku="V-DUP",
            color=self.color,
            size=self.size,
            finish=self.finish,
        )
        report = inspect_tile_catalog()
        self.assertIn("duplicate_combination", {issue.code for issue in report.blocking_issues})
        with self.assertRaises(TileReconciliationBlocked):
            self.apply()
        self.assertEqual(ProductType.objects.count(), 0)

    def test_missing_required_dimension_blocks_before_apply(self):
        self.variant.finish = None
        self.variant.save(update_fields=["finish"])
        report = inspect_tile_catalog()
        self.assertIn("missing_finish", {issue.code for issue in report.blocking_issues})
        with self.assertRaises(TileReconciliationBlocked):
            self.apply()

    def test_conflicting_product_type_blocks(self):
        other = ProductType.objects.create(code="mobile", name="Mobile")
        self.product.product_type = other
        self.product.save(update_fields=["product_type"])
        with self.assertRaises(TileReconciliationBlocked):
            self.apply()

    def test_conflicting_option_identity_blocks(self):
        definition = AttributeDefinition.objects.create(
            code="brand", name="Brand", name_fa="برند", data_type="option"
        )
        AttributeOption.objects.create(
            definition=definition, code="atlas", name="Different identity"
        )
        report = inspect_tile_catalog()
        self.assertIn("option_identity_conflict", {issue.code for issue in report.blocking_issues})

    def test_conflicting_product_and_variant_values_block(self):
        self.apply()
        brand_binding = ProductTypeAttribute.objects.get(definition__code="brand")
        nova = AttributeOption.objects.get(definition__code="brand", code="nova")
        ProductAttributeValue.objects.filter(
            product=self.product, binding=brand_binding
        ).update(option_value=nova)
        color_binding = ProductTypeAttribute.objects.get(definition__code="color")
        white = AttributeOption.objects.get(definition__code="color", code="white")
        VariantAttributeValue.objects.filter(
            variant=self.variant, binding=color_binding
        ).update(option_value=white)
        codes = {issue.code for issue in inspect_tile_catalog().blocking_issues}
        self.assertIn("product_value_conflict", codes)
        self.assertIn("variant_value_conflict", codes)

    def test_conflicting_existing_signature_blocks(self):
        self.variant.combination_signature = "color=wrong|finish=matte|size=60x60"
        self.variant.save(update_fields=["combination_signature"])
        report = inspect_tile_catalog()
        self.assertIn("signature_conflict", {issue.code for issue in report.blocking_issues})

    def test_exact_parity_is_accepted_and_apply_is_idempotent(self):
        first = self.apply()
        counts = first.generic_counts
        state = (
            ProductType.objects.count(),
            AttributeDefinition.objects.count(),
            AttributeOption.objects.count(),
            ProductTypeAttribute.objects.count(),
            ProductAttributeValue.objects.count(),
            VariantAttributeValue.objects.count(),
            ProductTypeAttributeOption.objects.count(),
        )
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)
        second = self.apply()
        self.assertEqual(second.generic_counts, counts)
        self.assertEqual(
            state,
            (
                ProductType.objects.count(),
                AttributeDefinition.objects.count(),
                AttributeOption.objects.count(),
                ProductTypeAttribute.objects.count(),
                ProductAttributeValue.objects.count(),
                VariantAttributeValue.objects.count(),
                ProductTypeAttributeOption.objects.count(),
            ),
        )

    def test_missing_and_conflicting_tile_memberships_are_reconciled_safely(self):
        self.apply()
        membership = ProductTypeAttributeOption.objects.get(
            binding__definition__code="color", option__code="black"
        )
        membership.delete()
        dry_run = inspect_tile_catalog()
        self.assertFalse(dry_run.blocking_issues)
        self.assertIn(
            "tile_option_membership_missing", {issue.code for issue in dry_run.issues}
        )
        self.assertIn(
            "tile_option_membership_missing",
            {issue.code for issue in inspect_tile_catalog(require_complete_generic=True).blocking_issues},
        )
        self.apply()
        restored = ProductTypeAttributeOption.objects.get(
            binding__definition__code="color", option__code="black"
        )
        restored.is_active = False
        restored.save(update_fields=["is_active"])
        self.assertIn(
            "tile_option_membership_conflict",
            {issue.code for issue in inspect_tile_catalog().blocking_issues},
        )

    def test_unrelated_generic_domain_data_is_ignored_but_tile_membership_leak_blocks(self):
        self.apply()
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        battery = AttributeDefinition.objects.create(
            code="battery_capacity", name="Battery", data_type="integer"
        )
        ProductTypeAttribute.objects.create(
            product_type=mobile,
            definition=battery,
            scope="product",
            cardinality="single",
            sort_order=1,
        )
        brand = AttributeDefinition.objects.get(code="brand")
        apple = AttributeOption.objects.create(
            definition=brand, code="apple", name="Apple", sort_order=100
        )
        mobile_brand = ProductTypeAttribute.objects.create(
            product_type=mobile,
            definition=brand,
            scope="product",
            cardinality="single",
            sort_order=2,
        )
        ProductTypeAttributeOption.objects.create(
            binding=mobile_brand, option=apple, sort_order=1
        )
        mobile_product = Product.objects.create(
            product_type=mobile, name="Phone", slug="phone", sku="PHONE"
        )
        ProductAttributeValue.objects.create(
            product=mobile_product, binding=mobile_brand, option_value=apple
        )
        color = AttributeDefinition.objects.get(code="color")
        black = color.options.get(code="black")
        mobile_color = ProductTypeAttribute.objects.create(
            product_type=mobile,
            definition=color,
            scope="variant",
            cardinality="single",
            is_required=True,
            is_variant_dimension=True,
            include_in_purchase_snapshot=True,
            sort_order=1,
        )
        ProductTypeAttributeOption.objects.create(
            binding=mobile_color, option=black, sort_order=1
        )
        mobile_variant = ProductVariant.objects.create(
            product=mobile_product,
            sku="PHONE-BLACK",
            combination_signature="color=black",
        )
        VariantAttributeValue.objects.create(
            variant=mobile_variant, binding=mobile_color, option_value=black
        )
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)
        self.assertFalse(run_generic_catalog_read_audit().blocking_issues)

        tile_brand = ProductTypeAttribute.objects.get(
            product_type__code="tile", definition=brand
        )
        ProductTypeAttributeOption.objects.create(
            binding=tile_brand, option=apple, sort_order=999
        )
        self.assertIn(
            "unexpected_tile_option_membership",
            {issue.code for issue in inspect_tile_catalog().blocking_issues},
        )

    def test_shared_definitions_isolate_tile_mobile_and_unrestricted_options(self):
        self.apply()
        tile = ProductType.objects.get(code="tile")
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        unrestricted = ProductType.objects.create(code="unrestricted", name="Unrestricted")
        expected = {
            "brand": (("atlas", "nova"), ("apple", "samsung")),
            "color": (("black", "white"), ("midnight", "silver")),
        }
        for code, (tile_codes, mobile_codes) in expected.items():
            definition = AttributeDefinition.objects.get(code=code)
            tile_binding = tile.attribute_bindings.get(definition=definition)
            tile_binding.option_restrictions.exclude(option__code__in=tile_codes).delete()
            for option_code in mobile_codes:
                option = AttributeOption.objects.create(
                    definition=definition,
                    code=option_code,
                    name=option_code.title(),
                    sort_order=100,
                )
            mobile_binding = ProductTypeAttribute.objects.create(
                product_type=mobile,
                definition=definition,
                scope=tile_binding.scope,
                cardinality=tile_binding.cardinality,
                is_variant_dimension=tile_binding.is_variant_dimension,
                include_in_purchase_snapshot=tile_binding.include_in_purchase_snapshot,
                sort_order=tile_binding.sort_order,
            )
            for index, option_code in enumerate(mobile_codes, start=1):
                ProductTypeAttributeOption.objects.create(
                    binding=mobile_binding,
                    option=definition.options.get(code=option_code),
                    sort_order=index,
                )
            unrestricted_binding = ProductTypeAttribute.objects.create(
                product_type=unrestricted,
                definition=definition,
                scope=tile_binding.scope,
                cardinality=tile_binding.cardinality,
                is_variant_dimension=tile_binding.is_variant_dimension,
                include_in_purchase_snapshot=tile_binding.include_in_purchase_snapshot,
                sort_order=tile_binding.sort_order,
            )
            self.assertEqual(
                list(available_options_for_binding(tile_binding).values_list("code", flat=True)),
                list(tile_codes),
            )
            self.assertEqual(
                list(available_options_for_binding(mobile_binding).values_list("code", flat=True)),
                list(mobile_codes),
            )
            self.assertEqual(
                set(available_options_for_binding(unrestricted_binding).values_list("code", flat=True)),
                set(tile_codes) | set(mobile_codes),
            )

    def test_simulated_failure_rolls_back_all_generic_writes(self):
        def fail():
            raise RuntimeError("simulated failure")

        with self.assertRaises(RuntimeError):
            apply_tile_catalog(failure_hook=fail)
        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertIsNone(self.product.product_type_id)
        self.assertIsNone(self.variant.combination_signature)
        self.assertEqual(ProductType.objects.count(), 0)
        self.assertEqual(AttributeDefinition.objects.count(), 0)

    def test_commerce_and_legacy_baseline_is_preserved(self):
        before = capture_preservation_baseline()
        legacy = (
            self.product.brand_id,
            set(self.product.designs.values_list("id", flat=True)),
            set(self.product.materials.values_list("id", flat=True)),
            set(self.product.usages.values_list("id", flat=True)),
            self.variant.color_id,
            self.variant.size_id,
            self.variant.finish_id,
        )
        self.apply()
        self.product.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertEqual(capture_preservation_baseline(), before)
        self.assertEqual(
            legacy,
            (
                self.product.brand_id,
                set(self.product.designs.values_list("id", flat=True)),
                set(self.product.materials.values_list("id", flat=True)),
                set(self.product.usages.values_list("id", flat=True)),
                self.variant.color_id,
                self.variant.size_id,
                self.variant.finish_id,
            ),
        )
