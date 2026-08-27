from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import translation

from products.currency import Currency
from products.models import Category, Product, ProductType, ProductVariant, VariantPrice


class StorefrontV3CResultCountTests(TestCase):
    def source(self, path):
        return (Path(settings.BASE_DIR) / path).read_text(encoding="utf-8")

    def setUp(self):
        product_type = ProductType.objects.create(code="countable", name="Countable")
        self.category = Category.objects.create(name="Counted Products", slug="counted-products")
        for index in range(3):
            product = Product.objects.create(
                product_type=product_type,
                name=f"Counted Product {index + 1}",
                slug=f"counted-product-{index + 1}",
                sku=f"COUNT-{index + 1}",
                is_active=True,
            )
            product.categories.add(self.category)
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"COUNT-{index + 1}-A",
                price_unit=ProductVariant.PriceUnit.UNIT,
                stock=1,
                is_active=True,
            )
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.USD,
                amount=Decimal("10.00"),
            )

    def assert_responsive_count_contract(self, response, expected_text):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-catalog-result-count', count=2)
        self.assertContains(response, 'data-result-count-mode="compact"', count=1)
        self.assertContains(response, 'text-text-muted md:hidden', count=1)
        self.assertContains(response, 'data-result-count-mode="desktop"', count=1)
        self.assertContains(response, 'hidden text-sm text-text-muted md:block', count=1)
        self.assertContains(response, expected_text, count=2)
        self.assertNotContains(response, "product found")
        self.assertNotContains(response, "products found")

    def test_all_products_uses_one_count_per_responsive_mode_with_pluralization(self):
        self.assert_responsive_count_contract(self.client.get("/en/shop/"), "3 products")
        singular = self.client.get("/en/shop/", {"q": "Counted Product 1"})
        self.assert_responsive_count_contract(singular, "1 product")

    def test_category_and_search_share_the_responsive_count_contract(self):
        category = self.client.get(f"/en/shop/category/{self.category.slug}/")
        search = self.client.get("/en/shop/", {"q": "Counted Product"})
        for response in (category, search):
            self.assert_responsive_count_contract(response, "3 products")

    def test_persian_count_uses_localized_pluralized_copy(self):
        response = self.client.get("/fa/shop/")
        self.assert_responsive_count_contract(response, "\u06f3 \u0645\u062d\u0635\u0648\u0644")

    def test_drawer_actions_use_active_count_and_both_openers_share_one_dialog(self):
        search_only = self.client.get("/en/shop/", {"q": "Counted"})
        self.assertEqual(search_only.context["active_filter_count"], 0)
        self.assertContains(search_only, 'data-filter-drawer>', count=1)
        self.assertContains(search_only, 'id="filterDrawer"')
        self.assertContains(search_only, 'aria-labelledby="filterDrawerTitle"')
        self.assertContains(search_only, 'data-mobile-filter-trigger', count=1)
        self.assertContains(search_only, 'data-intermediate-filter-trigger', count=1)
        self.assertContains(search_only, "Apply Filters", count=2)
        self.assertNotContains(search_only, "Clear Filters")

        filtered = self.client.get("/en/shop/", {"min_price": "5"})
        self.assertEqual(filtered.context["active_filter_count"], 1)
        self.assertContains(filtered, "Clear Filters", count=1)
        self.assertContains(filtered, filtered.context["clear_all_url"])

    def test_drawer_source_finalizes_focus_inert_scroll_lock_and_breakpoint_reset(self):
        drawer = self.source("static/js/drawer.js")
        for contract in (
            'window.matchMedia("(min-width: 1280px)")',
            "openDrawer(button)",
            "setTriggerState(trigger)",
            "inertBackground()",
            "restoreBackground()",
            'document.body.classList.add("sf-scroll-locked")',
            'event.key !== "Tab"',
            'event.key === "Escape"',
            "opener.focus()",
            "restoreFocus: false, immediate: true",
        ):
            self.assertIn(contract, drawer)
        self.assertNotIn('openButtons.forEach((button) => button.setAttribute("aria-expanded", "true"))', drawer)

    def test_toolbar_has_logical_edge_padding_bounded_sort_and_full_chip_label(self):
        styles = self.source("theme/static_src/src/styles.css")
        sort = self.source("products/templates/products/components/sort_dropdown.html")
        chips = self.source("products/templates/products/components/active_filters.html")
        self.assertIn("padding-inline: 0.25rem", styles)
        self.assertIn("scroll-padding-inline: 0.25rem", styles)
        self.assertIn("max-w-[10.5rem]", sort)
        self.assertIn("chip.remove_label", chips)
        self.assertIn("max-w-44 truncate", chips)

    def test_contextual_empty_states_offer_correct_recovery(self):
        search = self.client.get("/en/shop/", {"q": "nothing-matches-v3c"})
        self.assertContains(search, 'data-catalog-empty-state')
        self.assertContains(search, "No results for")
        self.assertContains(search, "nothing-matches-v3c")
        self.assertContains(search, "Browse all products")
        self.assertNotContains(search, "Clear filters")

        filtered = self.client.get("/en/shop/", {"min_price": "999999"})
        self.assertContains(filtered, "No products match these filters")
        self.assertContains(filtered, "Clear filters")
        self.assertContains(filtered, filtered.context["clear_all_url"])

        empty_category = Category.objects.create(name="Empty Category", slug="empty-category-v3c")
        category = self.client.get(f"/en/shop/category/{empty_category.slug}/")
        self.assertContains(category, "No products in this category")
        self.assertContains(category, "Empty Category")
        self.assertContains(category, "Browse all products")

        Product.objects.update(is_active=False)
        generic = self.client.get("/en/shop/")
        self.assertContains(generic, "No products available")
        self.assertNotContains(generic, "Browse all products")

    def test_search_with_filters_uses_existing_clear_all_url(self):
        response = self.client.get(
            "/en/shop/", {"q": "nothing-matches-v3c", "min_price": "999999"}
        )
        self.assertContains(response, "Try clearing the active filters")
        self.assertContains(response, "Clear all")
        self.assertContains(response, response.context["clear_all_url"])

    def test_pagination_first_middle_last_single_and_query_preservation(self):
        query_string = (
            "q=generic&sort=price_desc&categories=portable&attr_maker=north"
            "&min_price=10&in_stock=1&on_sale=1"
        )

        def render(page_number, count=30, language="en"):
            page = Paginator(list(range(count)), 10).get_page(page_number)
            with translation.override(language), self.assertNumQueries(0):
                return render_to_string(
                    "products/components/pagination.html",
                    {"page_obj": page, "query_string": query_string},
                )

        first = render(1)
        self.assertNotIn("data-pagination-previous", first)
        self.assertIn("data-pagination-next", first)
        self.assertIn("page=2", first)
        for value in (
            "q=generic", "sort=price_desc", "categories=portable", "attr_maker=north",
            "min_price=10", "in_stock=1", "on_sale=1",
        ):
            self.assertIn(value, first)

        middle = render(2)
        self.assertIn("data-pagination-link", middle)
        self.assertIn("data-pagination-previous", middle)
        self.assertIn("data-pagination-next", middle)
        self.assertIn("page=1", middle)
        self.assertIn("page=3", middle)

        last = render(3)
        self.assertIn("data-pagination-previous", last)
        self.assertNotIn("data-pagination-next", last)
        self.assertNotIn("data-catalog-pagination", render(1, count=1))

        persian = render(2, language="fa")
        self.assertIn("directional-icon", persian)
        self.assertNotIn("\u2190", persian)
        self.assertNotIn("\u2192", persian)

    def test_new_v3c_templates_are_generic_and_perform_no_orm_reads(self):
        paths = (
            "products/templates/products/components/filter_drawer.html",
            "products/templates/products/components/catalog_empty_state.html",
            "products/templates/products/components/pagination.html",
        )
        source = "".join(self.source(path) for path in paths)
        for forbidden in (
            ">Tile<", ">Brand<", ">Material<", ">Color<", ">Size<", ">Finish<",
            ".objects", ".children", ".variants", ".prices",
        ):
            self.assertNotIn(forbidden, source)

        page = Paginator([], 12).get_page(1)
        with self.assertNumQueries(0):
            rendered = render_to_string(
                "products/components/catalog_empty_state.html",
                {
                    "page_obj": page,
                    "query": "",
                    "category": None,
                    "active_filter_count": 0,
                    "clear_all_url": "/en/shop/",
                },
            )
        self.assertIn("data-catalog-empty-state", rendered)
