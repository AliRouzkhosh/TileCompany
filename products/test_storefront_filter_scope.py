from decimal import Decimal

from django.test import RequestFactory, TestCase

from products.catalog_filter import build_generic_catalog, build_generic_filter_state
from products.catalog_domain import calculate_variant_combination_signature
from products.commerce_presentation import prepare_commerce_line_presentations
from products.variant_selector import compose_generic_variant_selector
from products.selectors import get_variant_price
from products.currency import Currency
from products.models import (
    AttributeDefinition, AttributeOption, Brand, Category, Product,
    ProductAttributeValue, ProductType, ProductTypeAttribute, ProductVariant,
    VariantAttributeValue, VariantPrice,
)


class StorefrontFilterScopeTests(TestCase):
    """The presentation policy is schema-driven, not category-slug-driven."""

    def setUp(self):
        self.factory = RequestFactory()
        self.tiles = Category.objects.create(name="Tiles", slug="scope-tiles")
        self.tools = Category.objects.create(name="Tools", slug="scope-tools")
        self.ceramic = ProductType.objects.create(code="scope-ceramic", name="Ceramic")
        self.adhesive = ProductType.objects.create(code="scope-adhesive", name="Adhesive")
        self.grout = ProductType.objects.create(code="scope-grout", name="Grout")
        self.north = Brand.objects.create(name="North", slug="scope-north")
        self.south = Brand.objects.create(name="South", slug="scope-south")

        self.material, self.material_options = self.schema(
            self.ceramic, "scope-material", "product", ("ceramic", "porcelain"),
        )
        self.size, self.size_options = self.schema(
            self.ceramic, "scope-size", "variant", ("small", "large", "unknown"),
            internal={"unknown"},
        )
        self.form, self.form_options = self.schema(
            self.adhesive, "scope-form", "product", ("powder", "paste"),
        )
        self.standard, self.standard_options = self.schema(
            self.adhesive, "scope-standard", "product", ("c1e", "c2e"),
        )
        self.base, self.base_options = self.schema(
            self.grout, "scope-base", "product", ("cement", "paste", "two-part"),
        )
        self.water, _ = self.schema(self.grout, "scope-water", "product", (), data_type="boolean")

        for index, material in enumerate(("ceramic", "ceramic", "ceramic", "porcelain", "porcelain")):
            self.product(self.ceramic, self.tiles, f"CER-{index}", self.material, material,
                         variant=(self.size, "large" if index == 3 else "small" if index != 4 else "unknown"))
        for index, form in enumerate(("powder", "powder", "powder", "paste", "paste")):
            product = self.product(self.adhesive, self.tools, f"ADH-{index}", self.form, form)
            if index < 2:
                ProductAttributeValue.objects.create(product=product, binding=self.standard, option_value=self.standard_options["c1e" if index == 0 else "c2e"])
        for index, base in enumerate(("cement", "paste", "two-part")):
            product = self.product(self.grout, self.tools, f"GRO-{index}", self.base, base)
            ProductAttributeValue.objects.create(product=product, binding=self.water, boolean_value=True)

    def schema(self, product_type, code, scope, option_codes, *, internal=(), data_type="option"):
        definition = AttributeDefinition.objects.create(code=code, name=code, data_type=data_type)
        binding = ProductTypeAttribute.objects.create(
            product_type=product_type, definition=definition, scope=scope,
            is_filterable=True, facet_mode="terms", is_variant_dimension=scope == "variant",
            is_required=scope == "variant", include_in_purchase_snapshot=scope == "variant",
        )
        options = {
            code: AttributeOption.objects.create(
                definition=definition, code=code, name=code,
                presentation_metadata={"internal": True} if code in internal else {},
            ) for code in option_codes
        }
        return binding, options

    def product(self, product_type, category, sku, binding, value, *, variant=None):
        product = Product.objects.create(
            product_type=product_type, brand=self.north if sku.endswith(("0", "2", "4")) else self.south,
            name=sku, slug=sku.lower(), sku=sku, is_active=True,
        )
        product.categories.add(category)
        ProductAttributeValue.objects.create(product=product, binding=binding, option_value=self.option(binding, value))
        item = ProductVariant.objects.create(product=product, sku=f"{sku}-V", stock=4, is_active=True)
        if variant:
            VariantAttributeValue.objects.create(variant=item, binding=variant[0], option_value=self.option(variant[0], variant[1]))
        VariantPrice.objects.create(variant=item, currency=Currency.USD, amount=Decimal("10"))
        return product

    def option(self, binding, code):
        return AttributeOption.objects.get(definition=binding.definition, code=code)

    def result(self, path="/", data=None, category=None):
        return build_generic_catalog(self.factory.get(path, data or {}), Currency.USD, category=category)

    def test_heterogeneous_universe_exposes_global_product_type_narrowing_only(self):
        result = self.result()
        self.assertEqual(result.facets, ())
        self.assertEqual({item["code"] for item in result.product_type_options}, {"scope-ceramic", "scope-adhesive", "scope-grout"})
        deep_link = self.result(data={"attr_scope-material": "ceramic"})
        self.assertEqual(deep_link.products.count(), 3)
        state = build_generic_filter_state(self.factory.get("/", {"attr_scope-material": "ceramic"}), deep_link, Currency.USD)
        self.assertEqual(state["active_filter_count"], 1)
        self.assertEqual(state["active_filter_chips"][0]["label"], "scope-material")

    def test_type_narrowing_uses_product_type_code_and_hides_internal_and_zero_choices(self):
        result = self.result(data={"product_type": "scope-ceramic"})
        facets = {facet["definition_code"]: facet for facet in result.facets}
        self.assertEqual(set(facets), {"scope-material", "scope-size"}, repr(result.all_facets))
        self.assertNotIn("unknown", {choice["code"] for choice in facets["scope-size"]["choices"]})
        self.assertEqual(result.products.count(), 5)

        zero = self.result(data={"product_type": "scope-ceramic", "attr_scope-material": "ceramic", "attr_scope-size": "large"})
        size = next(facet for facet in zero.facets if facet["definition_code"] == "scope-size")
        large = next(choice for choice in size["choices"] if choice["code"] == "large")
        self.assertEqual(large["count"], 0)
        self.assertTrue(large["selected"])

    def test_parent_category_is_heterogeneous_until_product_type_is_selected(self):
        result = self.result(category=self.tools)
        self.assertEqual(result.facets, ())
        narrowed = self.result(data={"product_type": "scope-adhesive"}, category=self.tools)
        self.assertEqual({facet["definition_code"] for facet in narrowed.facets}, {"scope-form"})
        self.assertEqual({item["slug"] for item in narrowed.brand_options}, {"scope-north", "scope-south"})

    def test_sparse_and_one_value_facets_are_not_eligible(self):
        adhesive = self.result(data={"product_type": "scope-adhesive"})
        self.assertEqual({facet["definition_code"] for facet in adhesive.facets}, {"scope-form"})
        grout = self.result(data={"product_type": "scope-grout"})
        self.assertEqual({facet["definition_code"] for facet in grout.facets}, {"scope-base"})

    def test_view_and_htmx_refresh_share_the_same_scope_policy_in_en_and_fa(self):
        broad = self.client.get("/en/shop/", HTTP_HX_REQUEST="true")
        self.assertEqual(broad.status_code, 200)
        self.assertContains(broad, 'data-filter-group="product-type"')
        self.assertNotContains(broad, 'data-filter-group="scope-material"')
        narrowed = self.client.get(
            "/en/shop/", {"product_type": "scope-adhesive"}, HTTP_HX_REQUEST="true",
        )
        self.assertContains(narrowed, 'data-filter-group="scope-form"')
        self.assertNotContains(narrowed, 'data-filter-group="scope-material"')
        persian = self.client.get("/fa/shop/", {"product_type": "scope-adhesive"})
        self.assertEqual(persian.status_code, 200)
        self.assertContains(persian, 'name="product_type" value="scope-adhesive" checked')

    def test_internal_dimension_is_preserved_for_identity_but_absent_from_public_detail_and_commerce(self):
        product = Product.objects.get(sku="CER-4")
        variant = product.variants.get()
        variant.combination_signature = calculate_variant_combination_signature(variant)
        variant.save(update_fields=["combination_signature"])
        context = compose_generic_variant_selector(
            product=product, request=self.factory.get("/"), currency=Currency.USD,
            variants=[variant], price_getter=get_variant_price,
        )
        self.assertEqual(context["variant_groups"], [])
        self.assertEqual(context["variants_data"][0]["attributes"]["scope-size"], "unknown")
        self.assertEqual(context["variants_data"][0]["detail_specifications"], [])
        line = type("Line", (), {"product": product, "variant": variant, "quantity": 1, "unit_price": Decimal("10"), "subtotal": Decimal("10"), "unit_reference_price": None, "currency": Currency.USD})()
        presentation = prepare_commerce_line_presentations([line], currency=Currency.USD)[variant.pk]
        self.assertEqual(presentation.variant_attributes, ())
        response = self.client.get(product.get_absolute_url())
        self.assertNotContains(response, ">Unknown<")
        self.assertNotContains(response, 'data-group="scope-size"')
