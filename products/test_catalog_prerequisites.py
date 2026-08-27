from decimal import Decimal

from django.test import RequestFactory, TestCase

from .catalog_domain import serialize_combination_signature
from .catalog_filter import build_generic_catalog
from .catalog_integrity import active_untyped_products
from .catalog_presentation import compose_product_detail_presentation, prepare_product_card_presentations
from .commerce_presentation import prepare_commerce_line_presentations, selling_unit_presentation
from .currency import Currency
from .models import (
    AttributeDefinition, AttributeOption, Brand, Category, Product,
    ProductAttributeValue, ProductType, ProductTypeAttribute, ProductVariant,
    VariantAttributeValue, VariantPrice,
)
from .publication_services import publish_product, publish_variant
from .purchase_snapshot import build_generic_v2_purchase_snapshot


class GenericCatalogPrerequisiteSmokeTests(TestCase):
    """Disposable schemas proving the next seed can remain entirely non-Tile."""

    def setUp(self):
        self.factory = RequestFactory()
        self.brand = Brand.objects.create(name="Atlas", name_fa="اطلس", slug="atlas")
        self.tiles = Category.objects.create(name="Tiles", slug="tiles")
        self.tools = Category.objects.create(name="Tile Tools", slug="tile-tools")
        self.adhesives = Category.objects.create(name="Adhesives", slug="adhesives", parent=self.tools)
        self.grouts = Category.objects.create(name="Grouts", slug="grouts", parent=self.tools)

    def option_binding(self, product_type, code, name, *, scope, dimension=False, filterable=True):
        definition = AttributeDefinition.objects.create(code=code, name=name, data_type="option")
        binding = ProductTypeAttribute.objects.create(
            product_type=product_type, definition=definition, scope=scope,
            is_required=True, is_variant_dimension=dimension,
            include_in_purchase_snapshot=True, is_filterable=filterable,
            facet_mode="terms" if filterable else None,
        )
        options = {}
        for value in ("standard", "white", "grey", "20kg", "matte", "porcelain", "floor"):
            option = AttributeOption.objects.create(definition=definition, code=value, name=value.title())
            binding.option_restrictions.create(option=option)
            options[value] = option
        return binding, options

    def make_product(self, product_type, category, sku, *, unit, product_binding, product_option, dimensions):
        product = Product.objects.create(
            product_type=product_type, brand=self.brand, name=sku, name_fa=f"فا {sku}",
            slug=sku.lower(), sku=sku, is_active=False,
        )
        product.categories.add(category)
        ProductAttributeValue.objects.create(
            product=product, binding=product_binding, option_value=product_option,
        )
        pairs = []
        for binding, option in dimensions:
            pairs.append((binding.definition.code, option.code))
        variant = ProductVariant.objects.create(
            product=product, sku=f"{sku}-V", price_unit=unit, stock=9,
            is_active=False, combination_signature=serialize_combination_signature(pairs),
        )
        for binding, option in dimensions:
            VariantAttributeValue.objects.create(variant=variant, binding=binding, option_value=option)
        VariantPrice.objects.create(variant=variant, currency="USD", amount=Decimal("12.50"))
        VariantPrice.objects.create(variant=variant, currency="IRT", amount=Decimal("1000000"))
        publish_variant(variant)
        publish_product(product)
        product.refresh_from_db()
        variant.refresh_from_db()
        return product, variant

    def test_ceramic_tile_adhesive_and_grout_are_generic_end_to_end(self):
        ceramic = ProductType.objects.create(code="ceramic-tile", name="Tile & Ceramic", name_fa="کاشی و سرامیک")
        adhesive = ProductType.objects.create(code="tile-adhesive", name="Tile Adhesive")
        grout = ProductType.objects.create(code="grout", name="Grout")
        self.assertNotEqual(ceramic.code, "tile")

        tile_material, material_options = self.option_binding(ceramic, "ceramic-material", "Material", scope="product")
        tile_size, size_options = self.option_binding(ceramic, "ceramic-size", "Size", scope="variant", dimension=True)
        tile_finish, finish_options = self.option_binding(ceramic, "ceramic-finish", "Finish", scope="variant", dimension=True)
        adhesive_class, class_options = self.option_binding(adhesive, "adhesive-class", "Adhesive class", scope="product")
        adhesive_weight, adhesive_weight_options = self.option_binding(adhesive, "adhesive-package-weight", "Package weight", scope="variant", dimension=True)
        grout_application, application_options = self.option_binding(grout, "grout-application", "Application", scope="product")
        grout_weight, grout_weight_options = self.option_binding(grout, "grout-package-weight", "Package weight", scope="variant", dimension=True)
        grout_color, grout_color_options = self.option_binding(grout, "grout-color", "Color", scope="variant", dimension=True)

        tile, tile_variant = self.make_product(ceramic, self.tiles, "CERAMIC", unit="box", product_binding=tile_material, product_option=material_options["porcelain"], dimensions=((tile_size, size_options["standard"]), (tile_finish, finish_options["matte"])))
        adhesive_product, adhesive_variant = self.make_product(adhesive, self.adhesives, "ADHESIVE", unit="bag", product_binding=adhesive_class, product_option=class_options["standard"], dimensions=((adhesive_weight, adhesive_weight_options["20kg"]),))
        grout_product, grout_variant = self.make_product(grout, self.grouts, "GROUT", unit="pack", product_binding=grout_application, product_option=application_options["floor"], dimensions=((grout_weight, grout_weight_options["20kg"]), (grout_color, grout_color_options["grey"])))

        for product, variant in ((tile, tile_variant), (adhesive_product, adhesive_variant), (grout_product, grout_variant)):
            self.assertEqual(product.brand, self.brand)
            self.assertIsNone(variant.color_id)
            self.assertIsNone(variant.size_id)
            self.assertIsNone(variant.finish_id)
            self.assertEqual(compose_product_detail_presentation(product, selected_variant=variant).source, "generic")
            self.assertEqual(build_generic_v2_purchase_snapshot(variant)["version"], 2)

        prepare_product_card_presentations([tile, adhesive_product, grout_product], currency=Currency.USD)
        self.assertEqual(adhesive_product.catalog_presentation.brand_label, "Atlas")
        self.assertEqual(selling_unit_presentation("bag", language="fa").singular_noun, "کیسه")
        self.assertEqual(selling_unit_presentation("bucket").plural_noun, "buckets")
        self.assertEqual(selling_unit_presentation("pack").plural_noun, "packs")

        adhesive_result = build_generic_catalog(self.factory.get("/", {"brand": "atlas"}), Currency.USD, category=self.adhesives)
        self.assertEqual(list(adhesive_result.products.values_list("pk", flat=True)), [adhesive_product.pk])
        self.assertEqual(adhesive_result.facets, ())
        grout_result = build_generic_catalog(self.factory.get("/"), Currency.USD, category=self.grouts)
        self.assertEqual(grout_result.facets, ())
        tools_result = build_generic_catalog(self.factory.get("/"), Currency.USD, category=self.tools)
        self.assertEqual(tools_result.facets, ())

        line = type("Line", (), {"product": adhesive_product, "variant": adhesive_variant, "quantity": 2, "unit_price": Decimal("12.50"), "subtotal": Decimal("25.00"), "unit_reference_price": None})()
        presentation = prepare_commerce_line_presentations([line], currency="USD")[adhesive_variant.pk]
        self.assertEqual(presentation.selling_unit.code, "bag")
        self.assertEqual(active_untyped_products().count(), 0)
