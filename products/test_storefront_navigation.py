import re
from pathlib import Path
from urllib.parse import urlsplit

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.translation import override

from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
)
from .storefront_navigation import build_storefront_navigation


class StorefrontNavigationDataTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.root = Category.objects.create(
            name="Floor tiles", name_fa="کاشی کف", slug="floor", sort_order=1
        )
        self.child = Category.objects.create(
            name="Porcelain", name_fa="پرسلان", slug="porcelain", parent=self.root, sort_order=1
        )
        self.unrepresented = Category.objects.create(name="Unrepresented", slug="unrepresented")
        Category.objects.create(name="Inactive", slug="inactive", is_active=False)
        Category.objects.create(name="Hidden", slug="hidden", show_in_navigation=False)

        self.product_type = ProductType.objects.create(code="ceramic-tile", name="Tile & Ceramic", name_fa="کاشی و سرامیک", sort_order=1)
        self.unrepresented_type = ProductType.objects.create(code="unrepresented", name="Unrepresented")
        self.brand = Brand.objects.create(name="Atlas", name_fa="اطلس", slug="atlas")
        Brand.objects.create(name="Unused", name_fa="بی‌استفاده", slug="unused")
        self.product = Product.objects.create(
            name="Active tile", slug="active-tile", sku="ACTIVE-1", product_type=self.product_type, brand=self.brand
        )
        self.product.categories.add(self.child)
        ProductVariant.objects.create(product=self.product, sku="ACTIVE-1-V", stock=1)
        self.application_definition = AttributeDefinition.objects.create(
            code="demo-application-area", name="Application Area", name_fa="فضای کاربرد",
            data_type=AttributeDefinition.DataType.OPTION,
        )
        self.application_binding = ProductTypeAttribute.objects.create(
            product_type=self.product_type, definition=self.application_definition,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            cardinality=ProductTypeAttribute.Cardinality.MULTIPLE,
            is_filterable=True, facet_mode=ProductTypeAttribute.FacetMode.TERMS,
            show_on_storefront=True,
        )
        self.kitchen = AttributeOption.objects.create(
            definition=self.application_definition, code="kitchen", name="Kitchen", name_fa="آشپزخانه",
        )
        self.internal_area = AttributeOption.objects.create(
            definition=self.application_definition, code="internal", name="Internal", presentation_metadata={"internal": True},
        )
        AttributeOption.objects.create(
            definition=self.application_definition, code="unused", name="Unused area",
        )
        ProductAttributeValue.objects.create(product=self.product, binding=self.application_binding, option_value=self.kitchen)
        ProductAttributeValue.objects.create(product=self.product, binding=self.application_binding, option_value=self.internal_area)

    def payload(self, language="en"):
        with override(language):
            return build_storefront_navigation(self.factory.get(f"/{language}/"))

    def test_active_navigation_hierarchy_and_localized_labels(self):
        english = self.payload("en")
        categories = english["sections"][0]
        self.assertEqual(categories["code"], "categories")
        self.assertEqual(categories["items"][0]["label"], "Floor tiles")
        self.assertEqual(categories["items"][0]["children"][0]["label"], "Porcelain")
        self.assertIn("categories=floor", categories["items"][0]["url"])
        self.assertIn("categories=porcelain", categories["items"][0]["children"][0]["url"])
        labels = str(categories)
        self.assertNotIn("Inactive", labels)
        self.assertNotIn("Hidden", labels)
        self.assertNotIn("Unrepresented", labels)

        persian = self.payload("fa")
        self.assertEqual(persian["sections"][0]["items"][0]["label"], "کاشی کف")
        self.assertEqual(persian["sections"][0]["items"][0]["children"][0]["label"], "پرسلان")

    def test_only_represented_public_application_areas_and_brands_are_linked(self):
        payload = self.payload()
        self.assertEqual([section["code"] for section in payload["sections"]], ["categories", "shop-by-space", "brands"])
        application_areas = payload["sections"][1]
        brands = payload["sections"][2]
        self.assertEqual([item["label"] for item in application_areas["items"]], ["Kitchen"])
        self.assertNotIn("Internal", str(application_areas))
        self.assertNotIn("Unused area", str(application_areas))
        self.assertIn("product_type=ceramic-tile", application_areas["items"][0]["url"])
        self.assertIn("attr_demo-application-area=kitchen", application_areas["items"][0]["url"])
        self.assertEqual([item["label"] for item in brands["items"]], ["Atlas"])
        self.assertNotIn("Unused", str(brands))
        self.assertIn("brand=atlas", brands["items"][0]["url"])

        persian = self.payload("fa")
        self.assertEqual(persian["sections"][1]["label"], "خرید بر اساس فضا")
        self.assertEqual(persian["sections"][1]["items"][0]["label"], "آشپزخانه")
        self.assertEqual(persian["sections"][2]["label"], "برندها")
        self.assertEqual(persian["sections"][2]["items"][0]["label"], "اطلس")
        self.assertIn("/fa/shop/?brand=atlas", persian["sections"][2]["items"][0]["url"])

    def test_inactive_product_does_not_represent_canonical_sources(self):
        dormant_brand = Brand.objects.create(name="Dormant", slug="dormant")
        dormant_type = ProductType.objects.create(code="dormant", name="Dormant")
        inactive_product = Product.objects.create(
            name="Inactive tile", slug="inactive-tile", sku="INACTIVE-1",
            product_type=dormant_type, brand=dormant_brand, is_active=False,
        )
        inactive_product.categories.add(self.unrepresented)
        self.assertNotIn("Dormant", str(self.payload()))

    def test_menu_links_use_the_existing_shop_filter_contract(self):
        payload = self.payload()
        category_url = payload["sections"][0]["items"][0]["url"]
        application_url = payload["sections"][1]["items"][0]["url"]
        brand_url = payload["sections"][2]["items"][0]["url"]
        for url, expected in (
            (category_url, "categories=floor"),
            (application_url, "product_type=ceramic-tile"),
            (brand_url, "brand=atlas"),
        ):
            self.assertEqual(urlsplit(url).path, "/en/shop/")
            self.assertIn(expected, url)
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn("attr_demo-application-area=kitchen", application_url)
        response = self.client.get(application_url)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        product_type_response = self.client.get("/en/shop/?product_type=ceramic-tile")
        self.assertEqual(product_type_response.status_code, 200)
        self.assertEqual(product_type_response.context["page_obj"].paginator.count, 1)

    def test_brands_are_capped_and_prioritized_by_public_product_count(self):
        priority = Brand.objects.create(name="Priority", slug="priority")
        for index in range(3):
            Product.objects.create(
                name=f"Priority {index}", slug=f"priority-{index}", sku=f"PRIORITY-{index}",
                product_type=self.product_type, brand=priority,
            )
        for index in range(11):
            brand = Brand.objects.create(name=f"Brand {index:02d}", slug=f"brand-{index:02d}")
            Product.objects.create(
                name=f"Brand product {index}", slug=f"brand-product-{index}", sku=f"BRAND-{index}",
                product_type=self.product_type, brand=brand,
            )
        brands = self.payload()["sections"][2]["items"]
        self.assertEqual(len(brands), 10)
        self.assertEqual(brands[0]["label"], "Priority")
        self.assertNotIn("Unused", str(brands))

    def test_payload_is_four_bounded_queries_then_request_cached(self):
        request = self.factory.get("/en/")
        with CaptureQueriesContext(connection) as captured:
            first = build_storefront_navigation(request)
        self.assertEqual(len(captured), 4)
        with CaptureQueriesContext(connection) as cached:
            second = build_storefront_navigation(request)
        self.assertEqual(len(cached), 0)
        self.assertIs(first, second)


class DesktopHeaderPresentationTests(TestCase):
    def source(self, relative_path):
        return (Path(__file__).resolve().parent.parent / relative_path).read_text(encoding="utf-8")

    def test_desktop_mega_menu_disclosure_and_sections_render_en_fa(self):
        for language, direction in (("en", "ltr"), ("fa", "rtl")):
            response = self.client.get(f"/{language}/")
            self.assertContains(response, f'<html lang="{language}" dir="{direction}">', html=False)
            self.assertContains(response, "data-shop-trigger")
            self.assertContains(response, 'aria-expanded="false"')
            self.assertContains(response, 'aria-controls="desktopShopMegaMenu"')
            self.assertContains(response, 'id="desktopShopMegaMenu"')
            self.assertContains(response, 'role="navigation"')
            self.assertContains(response, "mega-section-categories")
            self.assertContains(response, "mega-section-shop-by-space")
            self.assertContains(response, "mega-section-brands")
        self.assertContains(self.client.get("/en/"), "View all products")
        self.assertNotIn("<img", self.source("templates/components/navbar/shop_mega_menu.html"))

    def test_header_preserves_anonymous_authenticated_staff_and_cart_contracts(self):
        anonymous = self.client.get("/en/")
        self.assertContains(anonymous, 'href="/en/accounts/login/"')
        self.assertContains(anonymous, "data-cart-count", count=0)

        session = self.client.session
        session["cart"] = {"variant-1": 3}
        session.save()
        with_cart = self.client.get("/en/")
        self.assertContains(with_cart, "data-cart-count", count=2)
        self.assertEqual(
            len(re.findall(rb"data-cart-count[^>]*>\s*3\s*</span>", with_cart.content)),
            2,
        )

        user = User.objects.create_user("member", password="test-pass")
        self.client.force_login(user)
        authenticated = self.client.get("/en/")
        self.assertContains(authenticated, "data-avatar-toggle")
        self.assertContains(authenticated, 'method="post"')
        self.assertContains(authenticated, 'action="/en/accounts/logout/"')
        self.assertNotContains(authenticated, "Admin Dashboard")

        call_command("seed_dashboard_roles", verbosity=0)
        user.groups.add(Group.objects.get(name="Product Manager"))
        self.assertContains(self.client.get("/en/"), "Admin Dashboard")

    def test_desktop_controls_use_line_icons_and_theme_language_state(self):
        source = "\n".join(
            self.source(path)
            for path in (
                "templates/components/navbar/desktop_nav.html",
                "templates/components/navbar/avatar_dropdown.html",
                "notifications/templates/notifications/components/bell.html",
            )
        )
        for icon in ('data-lucide="bell"', 'data-lucide="shopping-bag"', 'data-lucide="moon"', 'data-lucide="user"'):
            self.assertIn(icon, source)
        for emoji in ("🛒", "🔔", "👤", "❤️", "📦", "🎫", "📊"):
            self.assertNotIn(emoji, source)
        response = self.client.get("/en/")
        self.assertContains(response, "data-theme-toggle")
        self.assertContains(response, 'aria-pressed="false"')
        self.assertContains(response, "Switch to Persian")

    def test_search_uses_localized_endpoint_translated_labels_and_accessibility_hooks(self):
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        self.assertContains(english, 'data-search-endpoint="/en/products/search-suggestions/"')
        self.assertContains(persian, 'data-search-endpoint="/fa/products/search-suggestions/"')
        self.assertContains(english, 'name="q"')
        self.assertContains(english, 'role="combobox"')
        self.assertContains(english, 'aria-autocomplete="list"')
        self.assertContains(english, 'aria-controls="desktopHeaderSearchSuggestions"')
        self.assertContains(english, 'data-filters-label="Filters"')
        self.assertContains(english, 'data-products-label="Products"')
        search_source = self.source("static/js/search.js")
        self.assertIn("container.dataset.searchEndpoint", search_source)
        self.assertNotIn("/products/search-suggestions/", search_source)
        self.assertNotIn('textContent = "Filters"', search_source)
        self.assertNotIn('textContent = "Products"', search_source)

    def test_disclosure_source_has_intent_keyboard_outside_and_overlay_coordination(self):
        source = self.source("static/js/navbar.js")
        for contract in (
            "const openDelay = 125", "const closeDelay = 225", 'event.key === "Escape"',
            'addEventListener("pointerenter"', 'addEventListener("focusin"',
            'addEventListener("pointerdown"', "storefront:header-overlay-open",
        ):
            self.assertIn(contract, source)

    def test_navigation_source_does_not_use_legacy_catalog_authority(self):
        source = self.source("products/storefront_navigation.py")
        self.assertIn("represented_brands", source)
        self.assertIn("represented_application_areas", source)
        self.assertNotIn("ProductType", source)
        self.assertNotIn("Product Groups", source)
