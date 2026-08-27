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
