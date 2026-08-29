from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantPrice,
)


class StorefrontV3BDesktopCatalogTests(TestCase):
    def source(self, path):
        return (Path(settings.BASE_DIR) / path).read_text(encoding="utf-8")

    def setUp(self):
        self.product_type = ProductType.objects.create(code="appliance", name="Appliance")
        self.category = Category.objects.create(name="Portable", slug="portable-v3b")

        brand_definition = AttributeDefinition.objects.create(
            code="maker", name="Maker", data_type=AttributeDefinition.DataType.OPTION
        )
        self.brand_binding = ProductTypeAttribute.objects.create(
            product_type=self.product_type,
            definition=brand_definition,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            is_filterable=True,
            facet_mode=ProductTypeAttribute.FacetMode.TERMS,
        )
        self.used_option = AttributeOption.objects.create(
            definition=brand_definition, code="north", name="North"
        )
        self.disabled_option = AttributeOption.objects.create(
            definition=brand_definition, code="south", name="South"
        )
        ProductTypeAttributeOption.objects.create(
            binding=self.brand_binding, option=self.used_option
        )
        ProductTypeAttributeOption.objects.create(
            binding=self.brand_binding, option=self.disabled_option
        )

        capacity_definition = AttributeDefinition.objects.create(
            code="capacity", name="Capacity", data_type=AttributeDefinition.DataType.DECIMAL
        )
        self.capacity_binding = ProductTypeAttribute.objects.create(
            product_type=self.product_type,
            definition=capacity_definition,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            is_filterable=True,
            facet_mode=ProductTypeAttribute.FacetMode.RANGE,
            sort_order=1,
        )

        self.product = Product.objects.create(
            product_type=self.product_type,
            name="Generic Appliance",
            slug="generic-appliance-v3b",
            sku="GENERIC-V3B",
            is_active=True,
        )
        self.product.categories.add(self.category)
        ProductAttributeValue.objects.create(
            product=self.product,
            binding=self.brand_binding,
            option_value=self.used_option,
        )
        ProductAttributeValue.objects.create(
            product=self.product,
            binding=self.capacity_binding,
            decimal_value=Decimal("12.5"),
        )
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="GENERIC-V3B-A",
            stock=3,
            is_active=True,
            price_unit=ProductVariant.PriceUnit.UNIT,
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.USD, amount=Decimal("80.00")
        )

    def test_sidebar_preserves_active_generic_filters_when_a_sparse_schema_is_suppressed(self):
        response = self.client.get(
            "/en/shop/",
            {
                "categories": self.category.slug,
                "attr_maker": self.used_option.code,
                "attr_capacity_min": "10",
                "in_stock": "1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-filter-group="categories"')
        self.assertContains(response, 'data-filter-group="product-type"')
        self.assertNotContains(response, 'data-filter-group="maker"')
        self.assertNotContains(response, 'data-filter-group="capacity"')
        self.assertContains(response, 'data-filter-group="availability"')
        self.assertContains(response, 'Remove filter: Maker North')
        self.assertContains(response, 'Remove filter: Capacity')
        self.assertContains(response, 'name="in_stock" value="1" checked')
        self.assertEqual(response.context["filter_facets"], ())

    def test_range_labels_names_toolbar_chips_and_clear_contract_are_preserved(self):
        response = self.client.get(
            "/en/shop/",
            {
                "attr_capacity_min": "10",
                "attr_capacity_max": "20",
                "min_price": "50",
                "sort": "price_asc",
            },
        )
        self.assertEqual(response.context["active_filter_count"], 3)
        self.assertContains(response, ">Minimum<", count=2)
        self.assertContains(response, ">Maximum<", count=2)
        for name in ("min_price", "max_price"):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'data-desktop-active-filters')
        self.assertContains(response, 'id="catalogSortDesktop"')
        self.assertContains(response, 'data-intermediate-filter-trigger')
        cleared = parse_qs(urlsplit(response.context["clear_all_url"]).query)
        self.assertEqual(cleared, {"sort": ["price_asc"]})

    def test_desktop_breakpoints_keep_one_filter_contract_and_preserve_v3a_rows(self):
        product_list = self.source("products/templates/products/components/catalog_workspace.html")
        drawer = self.source("products/templates/products/components/filter_drawer.html")
        toolbar = self.source("products/templates/products/components/shop_toolbar.html")
        self.assertIn("w-[17rem]", product_list)
        self.assertIn("xl:sticky", product_list)
        self.assertIn("xl:block", product_list)
        self.assertIn("md:grid-cols-2 lg:grid-cols-3", product_list)
        self.assertIn("data-mobile-product-results", product_list)
        self.assertIn("xl:hidden", drawer)
        self.assertIn("md:inline-flex xl:hidden", toolbar)
        self.assertEqual(toolbar.count("data-intermediate-filter-trigger"), 1)
        self.assertEqual(drawer.count('id="filterDrawer"'), 1)

    def test_category_selector_uses_canonical_urls_and_preserves_committed_state(self):
        hidden = Category.objects.create(
            name="Hidden", slug="hidden-v3b", show_in_navigation=False,
        )
        child = Category.objects.create(
            name="Child", slug="child-v3b", parent=self.category, sort_order=1,
        )
        self.product.categories.add(child)
        response = self.client.get("/en/shop/", [
            ("category", "old-category"), ("categories", "other-category"),
            ("q", "Generic"), ("sort", "price_asc"),
            ("attr_maker", self.used_option.code), ("page", "3"),
        ])
        self.assertEqual(response.status_code, 200)
        items = response.context["category_selector_items"]
        self.assertEqual([item.get("category", None) for item in items[1:]], [self.category, child])
        self.assertNotIn(hidden, [item.get("category") for item in items])

        selected = next(item for item in items if item.get("category") == self.category)
        selected_query = parse_qs(urlsplit(selected["url"]).query)
        self.assertEqual(urlsplit(selected["url"]).path, "/en/shop/")
        self.assertEqual(selected_query["categories"], [self.category.slug])
        self.assertNotIn("category", selected_query)
        self.assertNotIn("page", selected_query)
        self.assertEqual(selected_query["q"], ["Generic"])
        self.assertEqual(selected_query["sort"], ["price_asc"])
        self.assertEqual(selected_query["attr_maker"], [self.used_option.code])

        all_products = items[0]
        all_query = parse_qs(urlsplit(all_products["url"]).query)
        self.assertNotIn("categories", all_query)
        self.assertNotIn("category", all_query)
        self.assertNotIn("page", all_query)
        self.assertEqual(all_query["q"], ["Generic"])
        self.assertEqual(all_query["sort"], ["price_asc"])

    def test_category_selector_active_state_and_htmx_workspace_contract(self):
        single = self.client.get("/en/shop/", {"categories": self.category.slug})
        self.assertContains(single, 'data-catalog-category-selector')
        single_selector = single.content.decode().split('data-catalog-category-selector', 1)[1].split('</section>', 1)[0]
        self.assertEqual(single_selector.count('aria-current="page"'), 1)
        self.assertContains(single, f'name="categories" value="{self.category.slug}" checked')

        all_products = self.client.get("/en/shop/")
        all_selector = all_products.content.decode().split('data-catalog-category-selector', 1)[1].split('</section>', 1)[0]
        self.assertEqual(all_selector.count('aria-current="page"'), 1)
        self.assertIn("All Products", all_selector)

        other = Category.objects.create(name="Other", slug="other-v3b")
        multiple = self.client.get("/en/shop/", [
            ("categories", self.category.slug), ("categories", other.slug),
        ])
        selector = multiple.content.decode().split('data-catalog-category-selector', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('aria-current="page"', selector)

        htmx = self.client.get(
            "/en/shop/", {"categories": self.category.slug}, HTTP_HX_REQUEST="true",
        )
        self.assertContains(htmx, 'id="catalog-workspace"')
        self.assertContains(htmx, 'data-catalog-category-selector')
        self.assertContains(htmx, 'aria-current="page"', count=1)

    def test_category_selector_uses_category_media_and_preserves_neutral_all_products_hook(self):
        self.category.storefront_image = "storefront/categories/portable-v3b.jpg"
        self.category.save(update_fields=["storefront_image"])

        response = self.client.get("/en/shop/")

        self.assertContains(response, 'src="/media/storefront/categories/portable-v3b.jpg"')
        self.assertContains(response, 'alt="" loading="lazy" decoding="async"')
        self.assertContains(response, "catalog-category-selector-media--all")
        self.assertContains(response, "catalog-category-selector-label")
        self.assertContains(response, "catalog-category-selector-cue")

    def test_category_selector_heading_uses_translatable_template_source(self):
        selector = self.source("products/templates/products/components/category_selector.html")
        self.assertIn('{% trans "Shop by category" %}', selector)

    def test_v3b_templates_are_domain_neutral_and_catalog_queries_do_not_scale(self):
        paths = (
            "products/templates/products/product_list.html",
            "products/templates/products/components/generic_filters_sidebar.html",
            "products/templates/products/components/shop_toolbar.html",
        )
        source = "".join(self.source(path) for path in paths)
        for forbidden in (
            ">Material<", ">Finish<", ">Color<", ">Size<", ">Brand<",
            ".objects", ".children", ".variants", ".prices",
        ):
            self.assertNotIn(forbidden, source)

        def measure(extra):
            for index in range(extra):
                extra_product = Product.objects.create(
                    product_type=self.product_type,
                    name=f"Extra {index}",
                    slug=f"extra-v3b-{extra}-{index}",
                    sku=f"EXTRA-V3B-{extra}-{index}",
                    is_active=True,
                )
                extra_variant = ProductVariant.objects.create(
                    product=extra_product,
                    sku=f"EXTRA-V3B-{extra}-{index}-A",
                    stock=1,
                    is_active=True,
                    price_unit=ProductVariant.PriceUnit.UNIT,
                )
                VariantPrice.objects.create(
                    variant=extra_variant, currency=Currency.USD, amount=Decimal("10")
                )
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get("/en/shop/")
                self.assertEqual(response.status_code, 200)
            return len(queries)

        self.assertEqual(measure(0), measure(3))
