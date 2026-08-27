from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from products.currency import Currency
from products.models import (
    AttributeDefinition, AttributeOption, Brand, Category, Product,
    ProductAttributeValue, ProductType, ProductTypeAttribute, ProductVariant,
    VariantAttributeValue, VariantPrice,
)
from products.search.categories import grouped_suggestions
from products.search.engine import search_products
from products.search.suggestions import product_suggestions


class GenericSearchTests(TestCase):
    def setUp(self):
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")
        self.bindings = {}
        self.options = {}

    def binding(self, code, scope, data_type="option", *, product_type=None, order=0, card=False):
        definition, _ = AttributeDefinition.objects.get_or_create(
            code=code,
            defaults={"name": code.replace("_", " ").title(), "data_type": data_type},
        )
        product_type = product_type or self.mobile
        binding = ProductTypeAttribute.objects.create(
            product_type=product_type, definition=definition, scope=scope,
            is_searchable=True,
            is_filterable=data_type != "text",
            facet_mode=(
                ProductTypeAttribute.FacetMode.RANGE
                if data_type in {"integer", "decimal"}
                else ProductTypeAttribute.FacetMode.TERMS
                if data_type != "text" else None
            ),
            show_on_product_card=card, sort_order=order,
        )
        self.bindings[(product_type.code, code)] = binding
        return binding

    def option(self, binding, code, name=None, name_fa=""):
        option, _ = AttributeOption.objects.get_or_create(
            definition=binding.definition, code=code,
            defaults={"name": name or code, "name_fa": name_fa},
        )
        self.options[(binding.product_type.code, binding.definition.code, code)] = option
        return option

    def product(self, sku, name=None, product_type=None, **values):
        product_type = product_type or self.mobile
        product = Product.objects.create(
            product_type=product_type, name=name or sku, slug=sku.lower(), sku=sku,
            is_active=True,
        )
        for code, raw in values.items():
            binding = self.bindings[(product_type.code, code)]
            kwargs = {"product": product, "binding": binding}
            if binding.definition.data_type == "option":
                kwargs["option_value"] = self.options[(product_type.code, code, raw)]
            elif binding.definition.data_type == "text":
                kwargs["text_value"] = raw
            elif binding.definition.data_type == "decimal":
                kwargs["decimal_value"] = Decimal(str(raw))
            elif binding.definition.data_type == "integer":
                kwargs["integer_value"] = int(raw)
            else:
                kwargs["boolean_value"] = raw
            ProductAttributeValue.objects.create(**kwargs)
        return product

    def variant(self, product, sku, amount, *, active=True, stock=5, **values):
        variant = ProductVariant.objects.create(
            product=product, sku=sku, price_unit="unit", is_active=active, stock=stock,
        )
        for code, raw in values.items():
            binding = self.bindings[(product.product_type.code, code)]
            kwargs = {"variant": variant, "binding": binding}
            if binding.definition.data_type == "option":
                kwargs["option_value"] = self.options[(product.product_type.code, code, raw)]
            elif binding.definition.data_type == "text":
                kwargs["text_value"] = raw
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
        brand = self.binding("brand", "product", card=True)
        screen = self.binding("screen_size", "product", "decimal", order=1, card=True)
        tagline = self.binding("tagline", "product", "text", order=2)
        esim = self.binding("supports_esim", "product", "boolean", order=3)
        color = self.binding("color", "variant", order=4)
        ram = self.binding("ram", "variant", order=5)
        storage = self.binding("storage", "variant", order=6)
        for binding, values in (
            (brand, (("apple", "Apple"), ("samsung", "Samsung"))),
            (color, (("black", "Black"), ("white", "White"))),
            (ram, (("8gb", "8 GB"), ("12gb", "12 GB"))),
            (storage, (("128gb", "128 GB"), ("256gb", "256 GB"), ("512gb", "512 GB"))),
        ):
            for code, name in values:
                self.option(binding, code, name)
        return brand, screen, tagline, esim, color, ram, storage

    def create_iphone(self):
        self.mobile_schema()
        product = self.product(
            "IPHONE-17-PRO", "iPhone 17 Pro", brand="apple",
            screen_size="6.3", tagline="Titanium flagship", supports_esim=True,
        )
        black = self.variant(product, "IPH-B-8-256", 900, color="black", ram="8gb", storage="256gb")
        white = self.variant(product, "IPH-W-12-512", 1200, color="white", ram="12gb", storage="512gb")
        return product, black, white

    def test_core_product_attribute_numeric_and_variant_search(self):
        product, _, white = self.create_iphone()
        for query in ("iPhone", "Apple", "Titanium", "6.3", "12gb", "512 GB"):
            self.assertIn(product, search_products(query, currency=Currency.USD))
        result = search_products("white 12gb 512gb", currency=Currency.USD).get()
        self.assertEqual(result.search_target_variant_id, white.pk)

    def test_canonical_core_brand_searches_in_english_and_persian(self):
        product, _, _ = self.create_iphone()
        brand = Brand.objects.create(name="Atlas", name_fa="اطلس", slug="atlas-core-search")
        product.brand = brand
        product.save(update_fields=["brand"])
        self.assertEqual(list(search_products("Atlas").values_list("pk", flat=True)), [product.pk])
        self.assertEqual(list(search_products("اطلس").values_list("pk", flat=True)), [product.pk])

    def test_same_variant_cross_trap_and_mixed_core_variant_terms(self):
        product, _, white = self.create_iphone()
        trap = self.product(
            "TRAP", "Trap Phone", brand="apple", screen_size="6.3",
            tagline="Trap", supports_esim=True,
        )
        self.variant(trap, "TRAP-B", 500, color="black", ram="8gb", storage="256gb")
        self.variant(trap, "TRAP-W", 600, color="white", ram="12gb", storage="128gb")
        self.assertNotIn(trap, search_products("black 12gb", currency=Currency.USD))
        mixed = search_products("iphone white 12gb", currency=Currency.USD).get()
        self.assertEqual((mixed.pk, mixed.search_target_variant_id), (product.pk, white.pk))

    def test_variant_sku_targets_exact_variant_and_inactive_values_do_not_search(self):
        product, _, white = self.create_iphone()
        result = search_products(white.sku, currency=Currency.USD).get()
        self.assertEqual((result.pk, result.search_target_variant_id), (product.pk, white.pk))
        inactive = self.variant(product, "SECRET-SKU", 10, active=False, color="black", ram="12gb", storage="128gb")
        secret = self.options[("mobile", "storage", "128gb")]
        secret.name = "Secret Capacity"; secret.save(update_fields=["name"])
        self.assertFalse(search_products("SECRET-SKU", currency=Currency.USD).exists())
        # The same option is not used by another active Variant in this fixture.
        self.assertFalse(search_products("Secret Capacity", currency=Currency.USD).exists())

    def test_boolean_words_are_not_global_free_text_search(self):
        self.create_iphone()
        self.assertFalse(search_products("true", currency=Currency.USD).exists())

    def test_multiple_product_options_and_retired_current_values_remain_searchable(self):
        connectivity = self.binding("connectivity", "product")
        connectivity.cardinality = ProductTypeAttribute.Cardinality.MULTIPLE
        connectivity.save(update_fields=["cardinality"])
        five_g = self.option(connectivity, "5g", "5G")
        nfc = self.option(connectivity, "nfc", "NFC")
        product = self.product("CONNECTED")
        ProductAttributeValue.objects.create(product=product, binding=connectivity, option_value=five_g)
        ProductAttributeValue.objects.create(product=product, binding=connectivity, option_value=nfc, sort_order=1)
        self.variant(product, "CONNECTED-V", 10)
        nfc.is_active = False; nfc.save(update_fields=["is_active"])
        self.assertEqual(search_products("NFC", currency=Currency.USD).get(), product)
        suggested = [item["value"] for group in grouped_suggestions("NFC") for item in group["items"]]
        self.assertIn("nfc", suggested)

    def test_relevance_exact_sku_then_name_then_attribute(self):
        product, _, _ = self.create_iphone()
        named = self.product("OTHER", "Apple", brand="samsung", screen_size="5", tagline="Other", supports_esim=False)
        self.variant(named, "OTHER-V", 2000, color="black", ram="8gb", storage="128gb")
        exact_name = list(search_products("Apple", currency=Currency.USD))
        self.assertEqual(exact_name[0].pk, named.pk)
        exact_sku = list(search_products(product.sku, currency=Currency.USD))
        self.assertEqual(exact_sku[0].pk, product.pk)
        relevance = self.client.get("/en/shop/?q=Apple")
        self.assertEqual(relevance.context["current_filters"]["sort"], "relevance")
        self.assertEqual(relevance.context["page_obj"].object_list[0].pk, named.pk)
        priced = self.client.get("/en/shop/?q=Apple&sort=price_asc")
        self.assertEqual(priced.context["page_obj"].object_list[0].pk, product.pk)

    def test_laptop_uses_same_engine_with_cpu_gpu_ram_ssd(self):
        laptop = ProductType.objects.create(code="laptop", name="Laptop")
        brand = self.binding("brand", "product", product_type=laptop)
        cpu = self.binding("cpu", "product", product_type=laptop)
        gpu = self.binding("gpu", "product", product_type=laptop)
        color = self.binding("color", "variant", product_type=laptop)
        ram = self.binding("ram", "variant", product_type=laptop)
        ssd = self.binding("ssd", "variant", product_type=laptop)
        for binding, values in (
            (brand, ("lenovo",)), (cpu, ("i9",)), (gpu, ("rtx",)),
            (color, ("gray",)), (ram, ("32gb",)), (ssd, ("1tb",)),
        ):
            for value in values:
                self.option(binding, value, value.upper())
        product = self.product("LAPTOP", "Legion Pro", product_type=laptop, brand="lenovo", cpu="i9", gpu="rtx")
        variant = self.variant(product, "LEGION-32-1T", 2000, color="gray", ram="32gb", ssd="1tb")
        self.assertEqual(search_products("i9 RTX", currency=Currency.USD).get(), product)
        result = search_products("gray 32gb 1tb", currency=Currency.USD).get()
        self.assertEqual(result.search_target_variant_id, variant.pk)

    def test_shared_definition_search_is_owner_based_and_distinct(self):
        product, _, _ = self.create_iphone()
        tile = ProductType.objects.create(code="tile-test", name="Tile")
        tile_brand = self.binding("brand", "product", product_type=tile)
        self.option(tile_brand, "ariana", "Ariana")
        tile_product = self.product("TILE", "Tile Product", product_type=tile, brand="ariana")
        self.variant(tile_product, "TILE-V", 10)
        apple = list(search_products("Apple", currency=Currency.USD))
        self.assertEqual([item.pk for item in apple], [product.pk])
        self.assertEqual(len({item.pk for item in apple}), len(apple))

    def test_persian_option_search_and_autocomplete_use_stored_labels(self):
        brand, *_ = self.mobile_schema()
        apple = self.options[("mobile", "brand", "apple")]
        apple.name_fa = "اپل"; apple.save(update_fields=["name_fa"])
        product = self.product("FA-PHONE", "Phone", brand="apple", screen_size="6.3", tagline="Phone", supports_esim=True)
        self.variant(product, "FA-V", 10, color="black", ram="8gb", storage="128gb")
        with translation.override("fa"):
            self.assertEqual(search_products("اپل", currency=Currency.TOMAN).get(), product)
            groups = grouped_suggestions("اپل")
            item = next(item for group in groups for item in group["items"] if item["value"] == "apple")
            self.assertEqual(item["label"], "اپل")

    def test_autocomplete_uses_actual_values_canonical_urls_categories_and_targets(self):
        product, _, white = self.create_iphone()
        category = Category.objects.create(name="Phones", slug="phones")
        product.categories.add(category)
        groups = grouped_suggestions("Apple")
        item = next(item for group in groups for item in group["items"] if item.get("value") == "apple")
        self.assertEqual(parse_qs(urlsplit(item["url"]).query), {"attr_brand": ["apple"]})
        self.assertNotIn("samsung", [item.get("value") for group in groups for item in group["items"]])
        category_item = next(item for group in grouped_suggestions("Phones") for item in group["items"] if item["type"] == "category")
        self.assertIn("/shop/category/phones/", category_item["url"])
        suggestion = product_suggestions("512gb", Currency.USD)[0]
        self.assertIn(f"variant={white.pk}", suggestion["url"])

    def test_search_and_f3b_filters_category_and_target_precedence(self):
        product, black, white = self.create_iphone()
        root = Category.objects.create(name="Mobile", slug="mobile-category")
        child = Category.objects.create(name="Phones", slug="phones-child", parent=root)
        product.categories.add(child)
        response = self.client.get(
            "/en/shop/?q=iPhone&attr_ram=12gb&attr_storage=512gb"
        )
        self.assertEqual(response.context["filter_source"], "generic")
        self.assertEqual(response.context["page_obj"].object_list[0].target_variant_id, white.pk)
        # Search and F3B Variant terms must share one qualifying Variant.
        filtered = self.client.get("/en/shop/?q=white&attr_color=black")
        self.assertEqual(len(filtered.context["page_obj"]), 0)
        price = self.client.get("/en/shop/?q=white&max_price=1000")
        self.assertEqual(len(price.context["page_obj"]), 0)
        stocked = self.client.get("/en/shop/?q=white&in_stock=1")
        self.assertEqual(stocked.context["page_obj"].object_list[0].target_variant_id, white.pk)
        category = self.client.get("/en/shop/category/mobile-category/?q=iPhone")
        self.assertEqual(category.context["page_obj"].object_list[0].pk, product.pk)

    def test_fallback_and_mixed_missing_type_fail_safe(self):
        legacy = Product.objects.create(name="Legacy Search", slug="legacy-search", sku="LEGACY", is_active=True)
        ProductVariant.objects.create(product=legacy, sku="LEGACY-V", is_active=True)
        with self.assertLogs("products.search.engine", level="ERROR"):
            self.assertEqual(search_products("Legacy").get(), legacy)
        generic = Product.objects.create(product_type=self.mobile, name="Generic", slug="generic", sku="GENERIC", is_active=True)
        ProductVariant.objects.create(product=generic, sku="GENERIC-V", is_active=True)
        with self.assertLogs("products.search.engine", level="ERROR"):
            self.assertFalse(search_products("Generic").exists())

    def test_search_and_autocomplete_query_counts_do_not_scale_with_products(self):
        self.mobile_schema()
        def measure(count):
            Product.objects.all().delete()
            for index in range(count):
                product = self.product(
                    f"PHONE-{index}", f"Phone {index}", brand="apple", screen_size="6.3",
                    tagline="Fast phone", supports_esim=True,
                )
                self.variant(product, f"PHONE-{index}-V", 10, color="black", ram="8gb", storage="128gb")
            with CaptureQueriesContext(connection) as search_queries:
                list(search_products("Phone", currency=Currency.USD))
            with CaptureQueriesContext(connection) as autocomplete_queries:
                grouped_suggestions("Apple")
                product_suggestions("Phone", Currency.USD)
            return len(search_queries), len(autocomplete_queries)
        self.assertEqual(measure(1), measure(4))
