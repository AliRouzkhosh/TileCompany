from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from products.catalog_filter import (
    active_descendant_ids, build_generic_catalog, build_generic_filter_state,
    normalize_filter_selections, selected_category_slugs,
)
from products.currency import Currency
from products.models import (
    AttributeDefinition, AttributeOption, Brand, Category, Product,
    ProductAttributeValue, ProductType, ProductTypeAttribute,
    ProductTypeAttributeOption, ProductVariant, VariantAttributeValue,
    VariantPrice,
)


class GenericCatalogFilterTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")
        self.bindings = {}
        self.options = {}

    def binding(self, code, scope, data_type="option", *, facet="terms", product_type=None, order=0):
        definition, _ = AttributeDefinition.objects.get_or_create(
            code=code, defaults={"name": code.replace("_", " ").title(), "data_type": data_type}
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=product_type or self.mobile, definition=definition, scope=scope,
            is_filterable=True, facet_mode=facet, sort_order=order,
        )
        self.bindings[(product_type or self.mobile).code, code] = binding
        return binding

    def option(self, binding, code, label=None, *, allowed=True):
        option, _ = AttributeOption.objects.get_or_create(
            definition=binding.definition, code=code,
            defaults={"name": label or code.title()},
        )
        if allowed:
            ProductTypeAttributeOption.objects.get_or_create(binding=binding, option=option)
        self.options[(binding.product_type.code, binding.definition.code, code)] = option
        return option

    def product(self, sku, product_type=None, **values):
        product = Product.objects.create(
            product_type=product_type or self.mobile, name=sku, slug=sku.lower(), sku=sku,
            is_active=True,
        )
        for code, raw in values.items():
            binding = self.bindings[(product.product_type.code, code)]
            kwargs = {"product": product, "binding": binding}
            if binding.definition.data_type == "option":
                kwargs["option_value"] = self.options[(product.product_type.code, code, raw)]
            elif binding.definition.data_type == "decimal":
                kwargs["decimal_value"] = Decimal(str(raw))
            elif binding.definition.data_type == "integer":
                kwargs["integer_value"] = int(raw)
            else:
                kwargs["boolean_value"] = raw
            ProductAttributeValue.objects.create(**kwargs)
        return product

    def variant(self, product, sku, amount, *, stock=5, **values):
        variant = ProductVariant.objects.create(
            product=product, sku=sku, price_unit="unit", stock=stock, is_active=True,
        )
        for code, raw in values.items():
            binding = self.bindings[(product.product_type.code, code)]
            kwargs = {"variant": variant, "binding": binding}
            if binding.definition.data_type == "option":
                kwargs["option_value"] = self.options[(product.product_type.code, code, raw)]
            elif binding.definition.data_type == "decimal":
                kwargs["decimal_value"] = Decimal(str(raw))
            elif binding.definition.data_type == "integer":
                kwargs["integer_value"] = int(raw)
            else:
                kwargs["boolean_value"] = raw
            VariantAttributeValue.objects.create(**kwargs)
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal(str(amount)))
        return variant

    def mobile_schema(self):
        brand = self.binding("brand", "product", order=0)
        screen = self.binding("screen_size", "product", "decimal", facet="range", order=1)
        esim = self.binding("supports_esim", "product", "boolean", order=2)
        color = self.binding("color", "variant", order=3)
        ram = self.binding("ram", "variant", order=4)
        storage = self.binding("storage", "variant", order=5)
        weight = self.binding("weight", "variant", "decimal", facet="range", order=6)
        for binding, values in (
            (brand, ("apple", "samsung")), (color, ("black", "white")),
            (ram, ("8gb", "12gb")), (storage, ("128gb", "256gb")),
        ):
            for value in values:
                self.option(binding, value)
        return brand, screen, esim, color, ram, storage, weight

    def test_mobile_mixed_scope_or_and_same_variant_and_target_commerce(self):
        self.mobile_schema()
        good = self.product("GOOD", brand="apple", screen_size="6.3", supports_esim=True)
        self.variant(good, "GOOD-A", 900, color="black", ram="8gb", storage="256gb", weight="200")
        target = self.variant(good, "GOOD-B", 1000, color="black", ram="12gb", storage="256gb", weight="205")
        self.variant(good, "GOOD-C", 800, color="white", ram="12gb", storage="128gb", weight="180")
        trap = self.product("TRAP", brand="apple", screen_size="6.2", supports_esim=True)
        self.variant(trap, "TRAP-A", 700, color="black", ram="8gb", storage="256gb", weight="190")
        self.variant(trap, "TRAP-B", 750, color="white", ram="12gb", storage="128gb", weight="185")

        request = self.factory.get("/", [
            ("attr_brand", "apple"), ("attr_screen_size_min", "6"),
            ("attr_screen_size_max", "7"), ("attr_color", "black"),
            ("attr_color", "white"), ("attr_ram", "12gb"),
            ("attr_storage", "256gb"), ("attr_weight_max", "210"),
        ])
        result = build_generic_catalog(request, Currency.USD)
        self.assertEqual(list(result.products.values_list("pk", flat=True)), [good.pk])
        row = result.products.get()
        self.assertEqual(row.catalog_target_variant_id, target.pk)
        self.assertEqual(row.catalog_min_price, Decimal("1000"))
        self.assertNotIn(trap.pk, result.products.values_list("pk", flat=True))

    def test_core_brand_filter_is_separate_from_schema_facets_and_category_scoped(self):
        self.mobile_schema()
        tiles = Category.objects.create(name="Tiles", slug="tiles")
        tools = Category.objects.create(name="Tools", slug="tools")
        atlas = Brand.objects.create(name="Atlas", name_fa="اطلس", slug="atlas-core")
        nova = Brand.objects.create(name="Nova", slug="nova-core")
        tile = self.product("BRAND-TILE", brand="apple", screen_size="6", supports_esim=True)
        tile.categories.add(tiles)
        tile.brand = atlas
        tile.save(update_fields=["brand"])
        self.variant(tile, "BRAND-TILE-V", 10, color="black", ram="8gb", storage="128gb", weight="1")
        tool = self.product("BRAND-TOOL", brand="samsung", screen_size="6", supports_esim=True)
        tool.categories.add(tools)
        tool.brand = nova
        tool.save(update_fields=["brand"])
        self.variant(tool, "BRAND-TOOL-V", 10, color="white", ram="8gb", storage="128gb", weight="1")

        scoped = build_generic_catalog(self.factory.get("/", {"brand": "atlas-core"}), Currency.USD, category=tiles)
        self.assertEqual(list(scoped.products.values_list("pk", flat=True)), [tile.pk])
        self.assertEqual(scoped.selected_brands, ("atlas-core",))
        self.assertEqual([(row["slug"], row["label"]) for row in scoped.brand_options], [("atlas-core", "Atlas")])
        self.assertEqual(scoped.facets, ())

    def test_price_and_stock_share_the_qualifying_variant(self):
        self.mobile_schema()
        product = self.product("SCOPED", brand="apple", screen_size="6.1", supports_esim=False)
        self.variant(product, "BLACK", 1500, stock=0, color="black", ram="12gb", storage="256gb", weight="200")
        self.variant(product, "WHITE", 900, stock=4, color="white", ram="8gb", storage="128gb", weight="190")
        price = build_generic_catalog(
            self.factory.get("/", {"attr_color": "black", "max_price": "1000"}), Currency.USD,
        )
        stock = build_generic_catalog(
            self.factory.get("/", {"attr_color": "black", "in_stock": "1"}), Currency.USD,
        )
        self.assertFalse(price.products.exists())
        self.assertFalse(stock.products.exists())

    def test_boolean_false_is_value_and_unknown_parameters_are_ignored(self):
        self.mobile_schema()
        false_product = self.product("FALSE", brand="apple", screen_size="6", supports_esim=False)
        missing = Product.objects.create(product_type=self.mobile, name="Missing", slug="missing", sku="MISSING", is_active=True)
        self.variant(false_product, "FALSE-V", 10, color="black", ram="8gb", storage="128gb", weight="1")
        self.variant(missing, "MISSING-V", 10, color="black", ram="8gb", storage="128gb", weight="1")
        result = build_generic_catalog(
            self.factory.get("/", {"attr_supports_esim": "false", "attr_unknown": "x"}), Currency.USD,
        )
        self.assertEqual(list(result.products.values_list("pk", flat=True)), [false_product.pk])

    def test_facets_preserve_other_filters_and_same_variant_counts(self):
        self.mobile_schema()
        first = self.product("FIRST", brand="apple", screen_size="6", supports_esim=True)
        self.variant(first, "F-B12", 10, color="black", ram="12gb", storage="256gb", weight="1")
        second = self.product("SECOND", brand="apple", screen_size="6", supports_esim=True)
        self.variant(second, "S-B8", 10, color="black", ram="8gb", storage="128gb", weight="1")
        self.variant(second, "S-W12", 10, color="white", ram="12gb", storage="256gb", weight="1")
        result = build_generic_catalog(self.factory.get("/", {"attr_ram": "12gb"}), Currency.USD)
        color = next(facet for facet in result.facets if facet["definition_code"] == "color")
        counts = {choice["code"]: choice["count"] for choice in color["choices"]}
        self.assertEqual(counts["black"], 1)
        self.assertEqual(counts["white"], 1)

    def test_shared_definition_memberships_do_not_leak_across_types(self):
        brand, _, _, color, ram, _, _ = self.mobile_schema()
        tile = ProductType.objects.create(code="tile-test", name="Tile")
        tile_brand = self.binding("brand", "product", product_type=tile)
        tile_color = self.binding("color", "variant", product_type=tile)
        self.option(tile_brand, "ariana")
        self.option(tile_color, "beige")
        mobile = self.product("MOBILE", brand="apple", screen_size="6", supports_esim=True)
        self.variant(mobile, "MOBILE-V", 10, color="black", ram="12gb", storage="256gb", weight="1")
        tile_product = self.product("TILE", product_type=tile, brand="ariana")
        self.variant(tile_product, "TILE-V", 10, color="beige")

        mixed = build_generic_catalog(self.factory.get("/"), Currency.USD)
        self.assertEqual(mixed.facets, ())
        brand_facet = next(f for f in mixed.all_facets if f["definition_code"] == "brand")
        self.assertEqual({c["code"] for c in brand_facet["choices"]}, {"apple", "ariana"})
        samsung_only = build_generic_catalog(self.factory.get("/", {"attr_brand": "samsung"}), Currency.USD)
        self.assertNotIn(tile_product.pk, samsung_only.products.values_list("pk", flat=True))
        ram_only = build_generic_catalog(self.factory.get("/", {"attr_ram": "12gb"}), Currency.USD)
        self.assertEqual(list(ram_only.products.values_list("pk", flat=True)), [mobile.pk])

    def test_category_pages_include_active_descendants(self):
        self.mobile_schema()
        root = Category.objects.create(name="Mobile", slug="mobile")
        phones = Category.objects.create(name="Phones", slug="phones", parent=root)
        flagship = Category.objects.create(name="Flagship", slug="flagship", parent=phones)
        budget = Category.objects.create(name="Budget", slug="budget", parent=phones)
        hidden = Category.objects.create(name="Hidden", slug="hidden", parent=root, is_active=False)
        one = self.product("FLAG", brand="apple", screen_size="6", supports_esim=True)
        two = self.product("BUDGET", brand="samsung", screen_size="6", supports_esim=False)
        one.categories.add(flagship); two.categories.add(budget)
        self.variant(one, "FLAG-V", 10, color="black", ram="12gb", storage="256gb", weight="1")
        self.variant(two, "BUDGET-V", 10, color="white", ram="8gb", storage="128gb", weight="1")
        self.assertNotIn(hidden.pk, active_descendant_ids(root))
        root_result = build_generic_catalog(self.factory.get("/"), Currency.USD, category=root)
        phone_result = build_generic_catalog(self.factory.get("/"), Currency.USD, category=phones)
        flag_result = build_generic_catalog(self.factory.get("/"), Currency.USD, category=flagship)
        self.assertEqual(set(root_result.products.values_list("pk", flat=True)), {one.pk, two.pk})
        self.assertEqual(set(phone_result.products.values_list("pk", flat=True)), {one.pk, two.pk})
        self.assertEqual(list(flag_result.products.values_list("pk", flat=True)), [one.pk])

    def test_sidebar_categories_emit_localized_removable_chips_and_preserve_state(self):
        self.mobile_schema()
        floor = Category.objects.create(
            name="Floor tiles", name_fa="کاشی کف", slug="floor-tiles-chip"
        )
        floor_child = Category.objects.create(
            name="Porcelain floor", name_fa="کف پرسلان", slug="porcelain-floor-chip",
            parent=floor,
        )
        wall = Category.objects.create(
            name="Wall tiles", name_fa="کاشی دیوار", slug="wall-tiles-chip"
        )
        product = self.product(
            "CHIP-PRODUCT", brand="apple", screen_size="6", supports_esim=True
        )
        product.categories.add(floor_child, wall)
        variant = self.variant(
            product, "CHIP-VARIANT", 10, stock=3,
            color="black", ram="12gb", storage="256gb", weight="1",
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.TOMAN, amount=Decimal("10")
        )

        with translation.override("fa"):
            response = self.client.get("/fa/shop/", [
                ("categories", floor.slug), ("categories", wall.slug),
                ("attr_brand", "apple"), ("q", "CHIP"), ("sort", "newest"),
                ("min_price", "1"), ("max_price", "20"), ("in_stock", "1"),
                ("page", "4"),
            ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.pk for item in response.context["page_obj"]], [product.pk])
        category_chips = {
            chip["display_value"]: chip
            for chip in response.context["active_filter_chips"]
            if chip["display_value"] in {"کاشی کف", "کاشی دیوار"}
        }
        self.assertEqual(set(category_chips), {"کاشی کف", "کاشی دیوار"})

        removed = parse_qs(
            urlsplit(category_chips["کاشی کف"]["remove_url"]).query,
            keep_blank_values=True,
        )
        self.assertEqual(removed["categories"], [wall.slug])
        self.assertEqual(removed["attr_brand"], ["apple"])
        self.assertEqual(removed["q"], ["CHIP"])
        self.assertEqual(removed["sort"], ["newest"])
        self.assertEqual(removed["min_price"], ["1"])
        self.assertEqual(removed["max_price"], ["20"])
        self.assertEqual(removed["in_stock"], ["1"])
        self.assertNotIn("page", removed)

        singular = self.factory.get("/en/shop/", {"category": floor.slug})
        self.assertEqual(selected_category_slugs(singular), (floor.slug,))
        with translation.override("en"):
            result = build_generic_catalog(singular, Currency.USD)
            state = build_generic_filter_state(singular, result, Currency.USD)
        self.assertEqual(
            [chip["display_value"] for chip in state["active_filter_chips"]],
            ["Floor tiles"],
        )

    def test_repeated_parameters_and_legacy_alias_normalize_to_same_contract(self):
        self.mobile.code = "tile"; self.mobile.save(update_fields=["code"])
        self.mobile_schema()
        canonical, _ = normalize_filter_selections(
            self.factory.get("/", [("attr_color", "black"), ("attr_color", "white")]),
            Product.objects.none() | Product.objects.filter(product_type=self.mobile),
        )
        legacy, _ = normalize_filter_selections(
            self.factory.get("/", [("colors", "black"), ("colors", "white")]),
            Product.objects.none() | Product.objects.filter(product_type=self.mobile),
        )
        self.assertEqual(canonical, legacy)

    def test_legacy_alias_and_canonical_queries_return_same_products(self):
        self.mobile.code = "tile"; self.mobile.save(update_fields=["code"])
        self.mobile_schema()
        black = self.product("BLACK-P", brand="apple", screen_size="6", supports_esim=True)
        white = self.product("WHITE-P", brand="apple", screen_size="6", supports_esim=True)
        self.variant(black, "BLACK-P-V", 10, color="black", ram="8gb", storage="128gb", weight="1")
        self.variant(white, "WHITE-P-V", 10, color="white", ram="8gb", storage="128gb", weight="1")
        canonical = build_generic_catalog(self.factory.get("/", {"attr_color": "black"}), Currency.USD)
        legacy = build_generic_catalog(self.factory.get("/", {"colors": "black"}), Currency.USD)
        self.assertEqual(
            list(canonical.products.values_list("pk", "catalog_target_variant_id")),
            list(legacy.products.values_list("pk", "catalog_target_variant_id")),
        )

    def test_multiple_product_values_use_or_semantics_and_retired_current_is_visible(self):
        material = self.binding("material", "product")
        material.cardinality = ProductTypeAttribute.Cardinality.MULTIPLE
        material.save(update_fields=["cardinality"])
        porcelain = self.option(material, "porcelain")
        ceramic = self.option(material, "ceramic")
        product = self.product("MULTI")
        ProductAttributeValue.objects.create(product=product, binding=material, option_value=porcelain)
        ProductAttributeValue.objects.create(product=product, binding=material, option_value=ceramic, sort_order=1)
        variant = ProductVariant.objects.create(product=product, sku="MULTI-V", is_active=True, price_unit="unit")
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=10)
        porcelain.is_active = False; porcelain.save(update_fields=["is_active"])
        result = build_generic_catalog(
            self.factory.get("/", [("attr_material", "porcelain"), ("attr_material", "ceramic")]), Currency.USD,
        )
        self.assertEqual(list(result.products.values_list("pk", flat=True)), [product.pk])
        facet = result.facets[0]
        retired = next(choice for choice in facet["choices"] if choice["code"] == "porcelain")
        self.assertTrue(retired["is_retired"])
        self.assertTrue(retired["selected"])

    def test_inactive_variants_are_excluded(self):
        self.mobile_schema()
        product = self.product("INACTIVE", brand="apple", screen_size="6", supports_esim=True)
        variant = self.variant(product, "INACTIVE-V", 10, color="black", ram="12gb", storage="256gb", weight="1")
        variant.is_active = False; variant.save(update_fields=["is_active"])
        result = build_generic_catalog(self.factory.get("/", {"attr_ram": "12gb"}), Currency.USD)
        self.assertFalse(result.products.exists())

    def test_generic_chips_remove_one_repeated_value_and_clear_preserves_sort(self):
        self.mobile_schema()
        product = self.product("CHIPS", brand="apple", screen_size="6", supports_esim=True)
        self.variant(product, "CHIPS-V", 10, color="black", ram="12gb", storage="256gb", weight="1")
        request = self.factory.get("/en/shop/", [
            ("attr_color", "black"), ("attr_color", "white"),
            ("attr_screen_size_min", "5.5"), ("sort", "price_asc"), ("page", "3"),
        ])
        result = build_generic_catalog(request, Currency.USD)
        state = build_generic_filter_state(request, result, Currency.USD)
        black = next(chip for chip in state["active_filter_chips"] if chip["display_value"] == "Black")
        removed = parse_qs(urlsplit(black["remove_url"]).query)
        self.assertEqual(removed["attr_color"], ["white"])
        self.assertNotIn("page", removed)
        cleared = parse_qs(urlsplit(state["clear_all_url"]).query)
        self.assertEqual(cleared, {"sort": ["price_asc"]})

    def test_query_count_does_not_scale_with_products(self):
        self.mobile_schema()
        def measure(count):
            Product.objects.all().delete()
            for index in range(count):
                product = self.product(f"P{index}", brand="apple", screen_size="6", supports_esim=True)
                self.variant(product, f"V{index}", 10, color="black", ram="8gb", storage="128gb", weight="1")
            with CaptureQueriesContext(connection) as queries:
                result = build_generic_catalog(self.factory.get("/"), Currency.USD)
                list(result.products)
            return len(queries)
        self.assertEqual(measure(1), measure(4))

    def test_production_catalog_view_uses_generic_sidebar_and_target_variant(self):
        self.mobile_schema()
        product = self.product("LIVE", brand="apple", screen_size="6.4", supports_esim=True)
        target = self.variant(product, "LIVE-V", 500, color="black", ram="12gb", storage="256gb", weight="200")
        response = self.client.get("/en/shop/?attr_color=black&attr_ram=12gb")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filter_source"], "generic")
        self.assertEqual(response.context["page_obj"].object_list[0].target_variant_id, target.pk)
        self.assertContains(response, 'name="attr_color"')
        self.assertContains(response, 'name="attr_ram"')

    def test_non_tile_integrity_failure_fails_closed_without_legacy_mixing(self):
        shared = self.binding("shared_filter", "product")
        self.option(shared, "one")
        laptop = ProductType.objects.create(code="laptop", name="Laptop")
        self.binding("shared_filter", "variant", product_type=laptop)
        mobile_product = self.product("BROKEN-MOBILE")
        laptop_product = self.product("BROKEN-LAPTOP", product_type=laptop)
        self.variant(mobile_product, "BROKEN-MOBILE-V", 10)
        self.variant(laptop_product, "BROKEN-LAPTOP-V", 10)
        with self.assertLogs("products.views.catalog", level="ERROR"):
            response = self.client.get("/en/shop/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filter_source"], "unavailable")
        self.assertEqual(len(response.context["page_obj"]), 0)
