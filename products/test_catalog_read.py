from dataclasses import FrozenInstanceError
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.translation import override

from .catalog_backfill import apply_tile_catalog
from .catalog_domain import calculate_variant_combination_signature
from .catalog_read import (
    CARD_ATTRIBUTE_LIMIT,
    CatalogReadIntegrityError,
    read_product_attributes,
    read_product_card_attributes,
    read_product_detail,
    serialize_variant_selection,
    specification_bundle,
)
from .catalog_read_audit import run_generic_catalog_read_audit
from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
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
)


class GenericReadMixin:
    sequence = 0

    def make_type(self, code="mobile", name_fa=""):
        return ProductType.objects.create(
            code=code, name=code.replace("_", " ").title(), name_fa=name_fa
        )

    def make_definition(self, code, data_type="option", **kwargs):
        defaults = {"name": code.replace("_", " ").title(), "data_type": data_type}
        defaults.update(kwargs)
        return AttributeDefinition.objects.create(code=code, **defaults)

    def make_binding(self, product_type, definition, *, scope="product", **kwargs):
        defaults = {
            "scope": scope,
            "sort_order": ProductTypeAttribute.objects.filter(product_type=product_type).count() + 1,
        }
        if kwargs.get("is_variant_dimension"):
            defaults.update(
                is_required=True,
                include_in_purchase_snapshot=True,
            )
        defaults.update(kwargs)
        return ProductTypeAttribute.objects.create(
            product_type=product_type, definition=definition, **defaults
        )

    def make_option(self, definition, code, **kwargs):
        defaults = {"name": code.replace("-", " ").title()}
        defaults.update(kwargs)
        return AttributeOption.objects.create(definition=definition, code=code, **defaults)

    def make_product(self, product_type, prefix="P"):
        type(self).sequence += 1
        token = f"{prefix}-{type(self).sequence}"
        return Product.objects.create(
            name=token, slug=token.lower(), sku=token, product_type=product_type
        )

    def make_variant(self, product, prefix="V", **kwargs):
        type(self).sequence += 1
        return ProductVariant.objects.create(
            product=product, sku=f"{prefix}-{type(self).sequence}", **kwargs
        )

    def add_value(self, owner, binding, **carrier):
        model = ProductAttributeValue if isinstance(owner, Product) else VariantAttributeValue
        owner_field = "product" if isinstance(owner, Product) else "variant"
        return model.objects.create(**{owner_field: owner, "binding": binding, **carrier})

    def finalize(self, variant):
        variant.combination_signature = calculate_variant_combination_signature(variant)
        variant.save(update_fields=["combination_signature"])
        return variant


class AttributeDTOTests(GenericReadMixin, TestCase):
    def test_all_typed_values_boolean_false_and_english_formatting(self):
        product_type = self.make_type()
        product = self.make_product(product_type)
        cases = (
            ("brand", "option", {"option_value": None}),
            ("note", "text", {"text_value": "OLED"}),
            ("cores", "integer", {"integer_value": 8}),
            ("screen", "decimal", {"decimal_value": Decimal("6.500000")}),
            ("wireless", "boolean", {"boolean_value": True}),
            ("refurbished", "boolean", {"boolean_value": False}),
        )
        for code, data_type, carrier in cases:
            definition = self.make_definition(
                code, data_type, unit="inch" if code == "screen" else ""
            )
            binding = self.make_binding(product_type, definition)
            if data_type == "option":
                carrier["option_value"] = self.make_option(definition, "acme")
            self.add_value(product, binding, **carrier)

        bundle = read_product_attributes(product, language="en")
        values = {
            group.definition_code: group.values[0]
            for group in bundle.attributes
        }
        self.assertEqual(values["brand"].value_code, "acme")
        self.assertEqual(values["brand"].raw_value, "acme")
        self.assertEqual(values["note"].raw_value, "OLED")
        self.assertEqual(values["cores"].raw_value, 8)
        self.assertEqual(values["screen"].raw_value, Decimal("6.500000"))
        self.assertEqual(values["screen"].display_value, "6.5 inch")
        self.assertIs(values["wireless"].raw_value, True)
        self.assertIs(values["refurbished"].raw_value, False)
        self.assertTrue(values["refurbished"].display_value)
        self.assertNotEqual(
            values["wireless"].display_value,
            values["refurbished"].display_value,
        )

    def test_persian_localization_fallback_and_retired_value_display(self):
        product_type = self.make_type()
        product = self.make_product(product_type)
        localized = self.make_definition("color", name="Color", name_fa="رنگ")
        fallback = self.make_definition("brand", name="Brand", name_fa="")
        color_binding = self.make_binding(product_type, localized)
        brand_binding = self.make_binding(product_type, fallback)
        black = self.make_option(localized, "black", name="Black", name_fa="مشکی")
        acme = self.make_option(fallback, "acme", name="Acme", name_fa="")
        self.add_value(product, color_binding, option_value=black)
        self.add_value(product, brand_binding, option_value=acme)
        black.is_active = False
        black.save(update_fields=["is_active"])

        with override("fa"):
            bundle = read_product_attributes(product)
        groups = {group.definition_code: group for group in bundle.attributes}
        self.assertEqual(groups["color"].label, "رنگ")
        self.assertEqual(groups["color"].values[0].display_value, "مشکی")
        self.assertTrue(groups["color"].values[0].is_retired)
        self.assertEqual(groups["brand"].label, "Brand")
        self.assertEqual(groups["brand"].values[0].display_value, "Acme")

    def test_dtos_and_mappings_are_immutable(self):
        product_type = self.make_type()
        product = self.make_product(product_type)
        definition = self.make_definition("brand")
        binding = self.make_binding(product_type, definition)
        self.add_value(product, binding, option_value=self.make_option(definition, "acme"))
        bundle = read_product_attributes(product)
        with self.assertRaises(FrozenInstanceError):
            bundle.product_id = 99


class ProductBundleTests(GenericReadMixin, TestCase):
    def test_multiple_order_missing_optional_card_limit_and_spec_flags(self):
        product_type = self.make_type()
        product = self.make_product(product_type)
        bindings = {}
        for index, code in enumerate(("brand", "material", "screen", "unused"), start=1):
            definition = self.make_definition(code)
            bindings[code] = self.make_binding(
                product_type,
                definition,
                sort_order=index,
                cardinality="multiple" if code == "material" else "single",
                show_on_product_card=code != "unused",
                show_in_specifications=code in {"material", "screen"},
                show_on_storefront=True,
                include_in_purchase_snapshot=code == "material",
            )
        variant_only = self.make_definition("color")
        self.make_binding(
            product_type, variant_only, scope="variant",
            is_variant_dimension=True,
        )
        self.add_value(product, bindings["brand"], option_value=self.make_option(bindings["brand"].definition, "acme"))
        ceramic = self.make_option(bindings["material"].definition, "ceramic", sort_order=2)
        glass = self.make_option(bindings["material"].definition, "glass", sort_order=1)
        self.add_value(product, bindings["material"], option_value=ceramic, sort_order=2)
        self.add_value(product, bindings["material"], option_value=glass, sort_order=1)
        self.add_value(product, bindings["screen"], option_value=self.make_option(bindings["screen"].definition, "oled"))

        bundle = read_product_attributes(product)
        self.assertEqual([group.definition_code for group in bundle.attributes], ["brand", "material", "screen"])
        material = next(group for group in bundle.attributes if group.definition_code == "material")
        self.assertEqual([value.value_code for value in material.values], ["glass", "ceramic"])
        self.assertEqual(
            [group.definition_code for group in bundle.card_attributes],
            ["brand", "material"],
        )
        self.assertEqual(len(bundle.card_attributes), CARD_ATTRIBUTE_LIMIT)
        self.assertEqual(
            [group.definition_code for group in bundle.specification_attributes],
            ["material", "screen"],
        )
        self.assertEqual(
            [group.definition_code for group in bundle.snapshot_eligible_product_attributes],
            ["material"],
        )

    def test_missing_required_generic_value_is_reported_not_synthesized(self):
        product_type = self.make_type("tile")
        legacy_brand = Brand.objects.create(name="Legacy", slug="legacy")
        product = self.make_product(product_type)
        product.brand = legacy_brand
        product.save(update_fields=["brand"])
        definition = self.make_definition("brand")
        self.make_binding(product_type, definition, is_required=True)
        before = ProductAttributeValue.objects.count()
        with self.assertRaises(CatalogReadIntegrityError) as error:
            read_product_attributes(product)
        self.assertIn("missing required attribute brand", str(error.exception))
        self.assertEqual(ProductAttributeValue.objects.count(), before)


class VariantAndDetailTests(GenericReadMixin, TestCase):
    def make_mobile(self):
        mobile = self.make_type("mobile", name_fa="موبایل")
        product = self.make_product(mobile, "MOBILE")
        brand_definition = self.make_definition("brand", name_fa="برند")
        brand_binding = self.make_binding(
            mobile, brand_definition, show_on_product_card=True,
            show_in_specifications=True,
        )
        self.add_value(
            product, brand_binding,
            option_value=self.make_option(brand_definition, "acme", name_fa="اکمه"),
        )
        screen_definition = self.make_definition("screen_size", "decimal", unit="inch")
        screen_binding = self.make_binding(
            mobile, screen_definition, show_on_product_card=True,
            show_in_specifications=True,
        )
        self.add_value(product, screen_binding, decimal_value=Decimal("6.7"))

        bindings = {}
        options = {}
        for index, (code, values) in enumerate(
            (("color", ("black", "white")), ("ram", ("8gb", "12gb")), ("storage", ("128gb", "256gb"))),
            start=1,
        ):
            definition = self.make_definition(code)
            bindings[code] = self.make_binding(
                mobile, definition, scope="variant", sort_order=index,
                is_variant_dimension=True, show_in_specifications=True,
            )
            options[code] = {
                value: self.make_option(definition, value, sort_order=position)
                for position, value in enumerate(values, start=1)
            }
        warranty_definition = self.make_definition("warranty", "boolean")
        bindings["warranty"] = self.make_binding(
            mobile, warranty_definition, scope="variant", sort_order=4,
            show_in_specifications=True,
        )
        variants = []
        for suffix, selection in (
            ("A", {"color": "black", "ram": "12gb", "storage": "256gb"}),
            ("B", {"color": "white", "ram": "8gb", "storage": "128gb"}),
        ):
            variant = self.make_variant(product, f"M-{suffix}", stock=5)
            for code, option_code in selection.items():
                self.add_value(variant, bindings[code], option_value=options[code][option_code])
            self.add_value(variant, bindings["warranty"], boolean_value=suffix == "A")
            variants.append(self.finalize(variant))
        return product, bindings, options, variants

    def test_synthetic_mobile_uses_dynamic_groups_maps_and_scalar_variant_spec(self):
        product, bindings, options, variants = self.make_mobile()
        detail = read_product_detail(product)
        self.assertEqual(detail.product_type_code, "mobile")
        self.assertEqual(
            [group.definition_code for group in detail.variant_groups],
            ["color", "ram", "storage"],
        )
        self.assertEqual(
            dict(detail.variants[0].attributes),
            {"color": "black", "ram": "12gb", "storage": "256gb"},
        )
        warranty = next(
            value for value in detail.variants[0].attribute_values
            if value.definition_code == "warranty"
        )
        self.assertIs(warranty.raw_value, True)
        payload = serialize_variant_selection(detail)
        self.assertEqual([group["code"] for group in payload["groups"]], ["color", "ram", "storage"])
        self.assertEqual(payload["variants"][0]["attributes"]["ram"], "12gb")

    def test_option_restrictions_exclude_new_choices_but_keep_used_retired_values(self):
        product, bindings, options, variants = self.make_mobile()
        ProductTypeAttributeOption.objects.create(
            binding=bindings["ram"], option=options["ram"]["12gb"], is_active=True
        )
        options["color"]["black"].is_active = False
        options["color"]["black"].save(update_fields=["is_active"])
        retired_unused = self.make_option(
            bindings["color"].definition, "red", is_active=False
        )
        detail = read_product_detail(product)
        groups = {group.definition_code: group for group in detail.variant_groups}
        ram = {option.code: option for option in groups["ram"].options}
        self.assertEqual(set(ram), {"12gb", "8gb"})
        self.assertTrue(ram["12gb"].available)
        self.assertFalse(ram["8gb"].available)
        color = {option.code: option for option in groups["color"].options}
        self.assertIn("black", color)
        self.assertTrue(color["black"].is_retired)
        self.assertFalse(color["black"].available)
        self.assertNotIn(retired_unused.code, color)
        self.assertTrue(color["white"].available)
        self.assertEqual(
            next(item for item in detail.variants if item.id == variants[0].id).attributes["color"],
            "black",
        )

    def test_selected_variant_is_required_for_variant_specifications(self):
        product, _bindings, _options, _variants = self.make_mobile()
        detail = read_product_detail(product)
        without_selection = specification_bundle(detail)
        self.assertEqual(without_selection.variant_attributes, ())
        selected = specification_bundle(detail, selected_variant=detail.variants[1])
        self.assertEqual(
            {value.definition_code for value in selected.variant_attributes},
            {"color", "ram", "storage", "warranty"},
        )

    def test_zero_dimension_base_variant_has_no_fake_group(self):
        simple = self.make_type("simple")
        product = self.make_product(simple, "SIMPLE")
        variant = self.make_variant(product, "BASE", stock=1)
        self.finalize(variant)
        detail = read_product_detail(product)
        self.assertEqual(detail.variant_groups, ())
        self.assertEqual(detail.variants[0].combination_signature, "__base__")
        self.assertEqual(dict(detail.variants[0].attributes), {})
        self.assertEqual(serialize_variant_selection(detail)["groups"], [])

    def test_shadow_reads_issue_only_select_queries_and_do_not_heal(self):
        product, _bindings, _options, _variants = self.make_mobile()
        counts = (
            ProductAttributeValue.objects.count(),
            VariantAttributeValue.objects.count(),
        )
        with CaptureQueriesContext(connection) as captured:
            read_product_detail(product)
        statements = [query["sql"].lstrip().upper() for query in captured.captured_queries]
        self.assertTrue(statements)
        self.assertTrue(all(statement.startswith("SELECT") for statement in statements))
        self.assertEqual(
            counts,
            (ProductAttributeValue.objects.count(), VariantAttributeValue.objects.count()),
        )


class QueryCountTests(GenericReadMixin, TestCase):
    def test_product_card_batch_query_count_does_not_grow_per_product(self):
        product_type = self.make_type()
        definition = self.make_definition("brand")
        binding = self.make_binding(product_type, definition, show_on_product_card=True)
        option = self.make_option(definition, "acme")
        products = [self.make_product(product_type, "CARD") for _ in range(4)]
        for product in products:
            self.add_value(product, binding, option_value=option)

        with CaptureQueriesContext(connection) as one:
            read_product_card_attributes(products[:1])
        with CaptureQueriesContext(connection) as four:
            result = read_product_card_attributes(products)
        self.assertEqual(len(one), len(four))
        self.assertLessEqual(len(four), 3)
        self.assertEqual(len(result), 4)

    def test_product_detail_bundle_has_fixed_non_n_plus_one_baseline(self):
        product, _bindings, _options, _variants = VariantAndDetailTests.make_mobile(self)
        with CaptureQueriesContext(connection) as captured:
            detail = read_product_detail(product)
        self.assertEqual(len(detail.variants), 2)
        self.assertLessEqual(len(captured), 7)


class TileShadowAuditTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Atlas", slug="atlas")
        self.design = Design.objects.create(name="Stone", slug="stone")
        self.material = Material.objects.create(name="Ceramic", slug="ceramic")
        self.usage = Usage.objects.create(name="Floor", slug="floor")
        self.color = Color.objects.create(name="Black", slug="black")
        self.size = Size.objects.create(name="60x60", slug="60x60", length_mm=600, width_mm=600)
        self.finish = Finish.objects.create(name="Matte", slug="matte")
        self.product = Product.objects.create(
            name="Tile", slug="audit-tile", sku="AUDIT-P", brand=self.brand
        )
        self.product.designs.add(self.design)
        self.product.materials.add(self.material)
        self.product.usages.add(self.usage)
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="AUDIT-V", color=self.color,
            size=self.size, finish=self.finish, length_mm=600, width_mm=600,
        )
        apply_tile_catalog()
        self.product.refresh_from_db()
        self.variant.refresh_from_db()

    def test_tile_legacy_generic_parity_and_command_output(self):
        report = run_generic_catalog_read_audit()
        self.assertTrue(report.usable)
        self.assertEqual((report.products_examined, report.variants_examined), (1, 1))
        self.assertEqual(report.product_attribute_parity_failures, 0)
        self.assertEqual(report.variant_attribute_parity_failures, 0)
        self.assertEqual(report.signature_consistency_failures, 0)
        detail = read_product_detail(self.product)
        self.assertEqual(
            [group.definition_code for group in detail.variant_groups],
            ["color", "size", "finish"],
        )
        self.assertEqual(
            [group["code"] for group in serialize_variant_selection(detail)["groups"]],
            ["color", "size", "finish"],
        )
        output = StringIO()
        call_command("audit_generic_catalog_reads", stdout=output)
        self.assertIn("GENERIC READS USABLE", output.getvalue())

    def test_audit_marks_missing_generic_state_blocking(self):
        ProductAttributeValue.objects.filter(
            product=self.product, binding__definition__code="brand"
        ).delete()
        report = run_generic_catalog_read_audit()
        self.assertFalse(report.usable)
        self.assertGreater(report.product_attribute_parity_failures, 0)
        with self.assertRaises(CommandError):
            call_command("audit_generic_catalog_reads", stdout=StringIO())

    def test_audit_counts_signature_drift_explicitly(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(
            combination_signature="color=wrong"
        )
        report = run_generic_catalog_read_audit()
        self.assertFalse(report.usable)
        self.assertEqual(report.signature_consistency_failures, 1)
