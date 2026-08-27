from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .catalog_domain import (
    available_options_for_binding,
    calculate_variant_combination_signature,
    serialize_combination_signature,
)
from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Color,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
)
from .services import set_product_primary_category


class CatalogFoundationMixin:
    def make_type(self, code="tile"):
        return ProductType.objects.create(code=code, name=code.title())

    def make_definition(self, code="color", data_type="option", **kwargs):
        return AttributeDefinition.objects.create(
            code=code, name=code.title(), data_type=data_type, **kwargs
        )

    def make_binding(self, product_type, definition, **kwargs):
        defaults = {
            "scope": ProductTypeAttribute.Scope.PRODUCT,
            "cardinality": ProductTypeAttribute.Cardinality.SINGLE,
        }
        defaults.update(kwargs)
        return ProductTypeAttribute.objects.create(
            product_type=product_type, definition=definition, **defaults
        )

    def make_product(self, sku="P-1", product_type=None):
        return Product.objects.create(
            name=sku, slug=sku.lower(), sku=sku, product_type=product_type
        )

    def make_variant(self, product, sku="V-1"):
        return ProductVariant.objects.create(product=product, sku=sku)


class ProductTypeAndDefinitionTests(CatalogFoundationMixin, TestCase):
    def test_product_type_code_is_unique(self):
        self.make_type()
        duplicate = ProductType(code="tile", name="Other")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_existing_product_may_have_null_product_type(self):
        product = self.make_product()
        self.assertIsNone(product.product_type)

    def test_attribute_definition_code_is_unique_and_types_are_supported(self):
        expected = {"option", "text", "integer", "decimal", "boolean"}
        self.assertEqual({value for value, _ in AttributeDefinition.DataType.choices}, expected)
        self.make_definition()
        with self.assertRaises(ValidationError):
            AttributeDefinition(code="color", name="Again", data_type="text").full_clean()

    def test_validation_rules_reject_unknown_and_validate_ranges(self):
        invalid = AttributeDefinition(
            code="weight", name="Weight", data_type="decimal", validation_rules={"regex": ".*"}
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()
        invalid.validation_rules = {"min": 10, "max": 1}
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_option_codes_are_scoped_to_definition(self):
        color = self.make_definition("color")
        size = self.make_definition("size")
        AttributeOption.objects.create(definition=color, code="black", name="Black")
        AttributeOption.objects.create(definition=size, code="black", name="Black size")
        duplicate = AttributeOption(definition=color, code="black", name="Again")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()


class ApplicabilityTests(CatalogFoundationMixin, TestCase):
    def setUp(self):
        self.product_type = self.make_type()
        self.option_definition = self.make_definition()

    def assert_invalid_binding(self, **kwargs):
        binding = ProductTypeAttribute(
            product_type=self.product_type,
            definition=kwargs.pop("definition", self.option_definition),
            scope=kwargs.pop("scope", "variant"),
            **kwargs,
        )
        with self.assertRaises(ValidationError):
            binding.full_clean()

    def test_definition_is_unique_per_product_type(self):
        self.make_binding(self.product_type, self.option_definition)
        duplicate = ProductTypeAttribute(
            product_type=self.product_type,
            definition=self.option_definition,
            scope="product",
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_product_scope_cannot_be_dimension(self):
        self.assert_invalid_binding(
            scope="product", is_variant_dimension=True, is_required=True,
            include_in_purchase_snapshot=True,
        )

    def test_dimension_contract_requires_variant_option_single_required_snapshot(self):
        text_definition = self.make_definition("note", "text")
        self.assert_invalid_binding(
            definition=text_definition, is_variant_dimension=True, is_required=True,
            include_in_purchase_snapshot=True,
        )
        self.assert_invalid_binding(
            is_variant_dimension=True, is_required=True, cardinality="multiple",
            include_in_purchase_snapshot=True,
        )
        self.assert_invalid_binding(
            is_variant_dimension=True, is_required=False, include_in_purchase_snapshot=True
        )
        self.assert_invalid_binding(is_variant_dimension=True, is_required=True)

    def test_multiple_cardinality_is_option_only_and_variant_values_are_single(self):
        text_definition = self.make_definition("note", "text")
        self.assert_invalid_binding(
            definition=text_definition, scope="product", cardinality="multiple"
        )
        self.assert_invalid_binding(scope="variant", cardinality="multiple")

    def test_facet_mode_matches_definition_type(self):
        text_definition = self.make_definition("note", "text")
        integer_definition = self.make_definition("weight", "integer")
        self.assert_invalid_binding(
            definition=text_definition, scope="product", is_filterable=True, facet_mode="terms"
        )
        self.assert_invalid_binding(
            definition=self.option_definition, scope="product", is_filterable=True, facet_mode="range"
        )
        valid = ProductTypeAttribute(
            product_type=self.product_type,
            definition=integer_definition,
            scope="product",
            is_filterable=True,
            facet_mode="range",
        )
        valid.full_clean()


class OptionPolicyTests(CatalogFoundationMixin, TestCase):
    def setUp(self):
        self.product_type = self.make_type()
        self.definition = self.make_definition()
        self.black = AttributeOption.objects.create(
            definition=self.definition, code="black", name="Black"
        )
        self.white = AttributeOption.objects.create(
            definition=self.definition, code="white", name="White"
        )
        self.binding = self.make_binding(self.product_type, self.definition)

    def test_restriction_must_match_binding_definition_and_is_unique(self):
        other_definition = self.make_definition("size")
        other_option = AttributeOption.objects.create(
            definition=other_definition, code="large", name="Large"
        )
        invalid = ProductTypeAttributeOption(binding=self.binding, option=other_option)
        with self.assertRaises(ValidationError):
            invalid.full_clean()
        ProductTypeAttributeOption.objects.create(binding=self.binding, option=self.black)
        duplicate = ProductTypeAttributeOption(binding=self.binding, option=self.black)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_no_restrictions_means_all_active_definition_options(self):
        self.white.is_active = False
        self.white.save(update_fields=["is_active"])
        self.assertEqual(list(available_options_for_binding(self.binding)), [self.black])

    def test_restrictions_limit_available_options(self):
        ProductTypeAttributeOption.objects.create(binding=self.binding, option=self.white)
        self.assertEqual(list(available_options_for_binding(self.binding)), [self.white])

    def test_retired_existing_option_remains_readable_but_cannot_be_newly_assigned(self):
        product = self.make_product(product_type=self.product_type)
        value = ProductAttributeValue(
            product=product, binding=self.binding, option_value=self.black
        )
        value.full_clean()
        value.save()
        self.black.is_active = False
        self.black.save(update_fields=["is_active"])
        value.full_clean()
        other_product = self.make_product("P-2", self.product_type)
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=other_product, binding=self.binding, option_value=self.black
            ).full_clean()


class ProductAttributeValueTests(CatalogFoundationMixin, TestCase):
    def setUp(self):
        self.product_type = self.make_type()
        self.product = self.make_product(product_type=self.product_type)

    def test_owner_type_and_scope_are_validated(self):
        other_type = self.make_type("mobile")
        definition = self.make_definition("note", "text")
        wrong_type_binding = self.make_binding(other_type, definition)
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=self.product, binding=wrong_type_binding, text_value="x"
            ).full_clean()
        variant_binding = self.make_binding(
            self.product_type, self.make_definition("weight", "integer"), scope="variant"
        )
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=self.product, binding=variant_binding, integer_value=1
            ).full_clean()

    def test_typed_carrier_and_option_definition_are_validated(self):
        definition = self.make_definition("note", "text")
        binding = self.make_binding(self.product_type, definition)
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=self.product, binding=binding, integer_value=1
            ).full_clean()
        option_definition = self.make_definition("color")
        option_binding = self.make_binding(self.product_type, option_definition)
        wrong_option_definition = self.make_definition("size")
        wrong_option = AttributeOption.objects.create(
            definition=wrong_option_definition, code="large", name="Large"
        )
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=self.product, binding=option_binding, option_value=wrong_option
            ).full_clean()

    def test_boolean_false_is_an_explicit_value(self):
        definition = self.make_definition("waterproof", "boolean")
        binding = self.make_binding(self.product_type, definition)
        value = ProductAttributeValue(
            product=self.product, binding=binding, boolean_value=False
        )
        value.full_clean()
        value.save()
        self.assertIs(value.boolean_value, False)

    def test_single_cardinality_and_multiple_distinct_options(self):
        definition = self.make_definition("usage")
        first = AttributeOption.objects.create(definition=definition, code="wall", name="Wall")
        second = AttributeOption.objects.create(definition=definition, code="floor", name="Floor")
        single = self.make_binding(self.product_type, definition)
        first_value = ProductAttributeValue.objects.create(
            product=self.product, binding=single, option_value=first
        )
        with self.assertRaises(ValidationError):
            ProductAttributeValue(
                product=self.product, binding=single, option_value=second
            ).full_clean()
        first_value.delete()
        single.delete()
        multiple = self.make_binding(
            self.product_type, definition, cardinality="multiple"
        )
        for option in (first, second):
            value = ProductAttributeValue(
                product=self.product, binding=multiple, option_value=option
            )
            value.full_clean()
            value.save()
        self.assertEqual(self.product.attribute_values.count(), 2)


class VariantAttributeValueTests(CatalogFoundationMixin, TestCase):
    def setUp(self):
        self.product_type = self.make_type()
        self.product = self.make_product(product_type=self.product_type)
        self.variant = self.make_variant(self.product)

    def test_typed_scalar_non_dimension_variant_attribute_works(self):
        definition = self.make_definition(
            "weight", "decimal", validation_rules={"min": 0, "step": "0.1"}
        )
        binding = self.make_binding(self.product_type, definition, scope="variant")
        value = VariantAttributeValue(
            variant=self.variant, binding=binding, decimal_value=Decimal("2.5")
        )
        value.full_clean()
        value.save()

    def test_scope_is_validated(self):
        definition = self.make_definition("note", "text")
        binding = self.make_binding(self.product_type, definition)
        with self.assertRaises(ValidationError):
            VariantAttributeValue(
                variant=self.variant, binding=binding, text_value="x"
            ).full_clean()

    def test_option_dimension_and_one_value_per_dimension(self):
        definition = self.make_definition()
        binding = self.make_binding(
            self.product_type,
            definition,
            scope="variant",
            is_variant_dimension=True,
            is_required=True,
            include_in_purchase_snapshot=True,
        )
        black = AttributeOption.objects.create(definition=definition, code="black", name="Black")
        white = AttributeOption.objects.create(definition=definition, code="white", name="White")
        VariantAttributeValue.objects.create(
            variant=self.variant, binding=binding, option_value=black
        )
        duplicate = VariantAttributeValue(
            variant=self.variant, binding=binding, option_value=white
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_disallowed_option_is_rejected(self):
        definition = self.make_definition()
        binding = self.make_binding(self.product_type, definition, scope="variant")
        allowed = AttributeOption.objects.create(definition=definition, code="black", name="Black")
        disallowed = AttributeOption.objects.create(definition=definition, code="white", name="White")
        ProductTypeAttributeOption.objects.create(binding=binding, option=allowed)
        with self.assertRaises(ValidationError):
            VariantAttributeValue(
                variant=self.variant, binding=binding, option_value=disallowed
            ).full_clean()


class CategoryAndPrimaryCategoryTests(CatalogFoundationMixin, TestCase):
    def test_parent_hierarchy_and_flat_category_are_valid(self):
        root = Category.objects.create(name="Root", slug="root")
        child = Category(name="Child", slug="child", parent=root)
        child.full_clean()
        child.save()
        flat = Category(name="Flat", slug="flat")
        flat.full_clean()

    def test_self_parent_and_cycle_are_rejected(self):
        root = Category.objects.create(name="Root", slug="root")
        root.parent = root
        with self.assertRaises(ValidationError):
            root.full_clean()
        child = Category.objects.create(name="Child", slug="child", parent=root)
        root.parent = child
        with self.assertRaises(ValidationError):
            root.full_clean()

    def test_primary_category_service_accepts_member_and_rejects_nonmember(self):
        member = Category.objects.create(name="Member", slug="member")
        other = Category.objects.create(name="Other", slug="other")
        product = self.make_product()
        self.assertIsNone(product.primary_category)
        product.categories.add(member)
        set_product_primary_category(product=product, category=member)
        self.assertEqual(product.primary_category, member)
        with self.assertRaises(ValidationError):
            set_product_primary_category(product=product, category=other)


class CombinationSignatureTests(CatalogFoundationMixin, TestCase):
    def test_serializer_sorts_codes_and_base_is_stable(self):
        self.assertEqual(serialize_combination_signature([]), "__base__")
        self.assertEqual(
            serialize_combination_signature([("storage", "256gb"), ("color", "black")]),
            "color=black|storage=256gb",
        )

    def test_calculation_ignores_labels_and_display_order(self):
        product_type = self.make_type()
        product = self.make_product(product_type=product_type)
        variant = self.make_variant(product)
        storage_definition = self.make_definition("storage")
        color_definition = self.make_definition("color")
        storage_binding = self.make_binding(
            product_type, storage_definition, scope="variant", is_variant_dimension=True,
            is_required=True, include_in_purchase_snapshot=True, sort_order=0
        )
        color_binding = self.make_binding(
            product_type, color_definition, scope="variant", is_variant_dimension=True,
            is_required=True, include_in_purchase_snapshot=True, sort_order=100
        )
        storage = AttributeOption.objects.create(
            definition=storage_definition, code="256gb", name="Old", sort_order=100
        )
        black = AttributeOption.objects.create(
            definition=color_definition, code="black", name="Old", sort_order=0
        )
        VariantAttributeValue.objects.create(variant=variant, binding=storage_binding, option_value=storage)
        VariantAttributeValue.objects.create(variant=variant, binding=color_binding, option_value=black)
        self.assertEqual(calculate_variant_combination_signature(variant), "color=black|storage=256gb")
        storage.name = "New label"
        storage.sort_order = 0
        storage.save(update_fields=["name", "sort_order"])
        self.assertEqual(calculate_variant_combination_signature(variant), "color=black|storage=256gb")

    def test_zero_dimensions_calculate_base(self):
        product_type = self.make_type()
        variant = self.make_variant(self.make_product(product_type=product_type))
        self.assertEqual(calculate_variant_combination_signature(variant), "__base__")

    def test_signature_uniqueness_scope_and_null_compatibility(self):
        first_product = self.make_product("P-1")
        second_product = self.make_product("P-2")
        ProductVariant.objects.create(product=first_product, sku="V-1", combination_signature="__base__")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(
                product=first_product, sku="V-2", combination_signature="__base__"
            )
        ProductVariant.objects.create(product=second_product, sku="V-3", combination_signature="__base__")
        ProductVariant.objects.create(product=first_product, sku="V-4")
        ProductVariant.objects.create(product=first_product, sku="V-5")


class LegacyCompatibilityTests(CatalogFoundationMixin, TestCase):
    def test_legacy_product_and_variant_relations_remain_usable(self):
        brand = Brand.objects.create(name="Legacy", slug="legacy")
        color = Color.objects.create(name="Black", slug="black")
        product = Product.objects.create(name="Legacy", slug="legacy-product", sku="P-L", brand=brand)
        variant = ProductVariant.objects.create(product=product, sku="V-L", color=color)
        self.assertEqual(product.brand, brand)
        self.assertEqual(variant.color, color)
        self.assertIsNone(product.product_type)
        self.assertIsNone(variant.combination_signature)
        self.assertFalse(variant.attribute_values.exists())
