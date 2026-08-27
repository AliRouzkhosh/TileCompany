import re
from pathlib import Path

from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.management import call_command
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.translation import override

from notifications.models import Notification, NotificationEvent

from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
)
from .storefront_navigation import build_storefront_navigation


class MobileStorefrontShellTests(TestCase):
    def source(self, relative_path):
        return (Path(__file__).resolve().parent.parent / relative_path).read_text(encoding="utf-8")

    def test_mobile_header_is_64px_and_contains_only_prioritized_controls(self):
        source = self.source("templates/components/navbar/mobile_nav.html")
        self.assertIn("h-16", source)
        self.assertIn("data-mobile-menu-open", source)
        self.assertIn("data-mobile-search-open", source)
        self.assertIn('data-lucide="shopping-bag"', source)
        self.assertIn("data-cart-count", source)
        for excluded in ("data-theme-toggle", "data-notification-menu", 'data-lucide="bell"', "accounts:profile", "accounts:login"):
            self.assertNotIn(excluded, source)
        for emoji in ("☰", "🔍", "🛒", "🔔", "👤", "❤️", "📦", "🎫", "📊"):
            self.assertNotIn(emoji, source)

    def test_mobile_header_and_drawer_render_in_en_ltr_and_fa_rtl(self):
        for language, direction in (("en", "ltr"), ("fa", "rtl")):
            response = self.client.get(f"/{language}/")
            self.assertContains(response, f'<html lang="{language}" dir="{direction}">', html=False)
            self.assertContains(response, 'aria-controls="mobileDrawer"')
            self.assertContains(response, 'aria-controls="mobileSearchPanel"')
            self.assertContains(response, "data-mobile-drawer-layer")
            self.assertContains(response, "data-mobile-search-layer")

    def test_mobile_cart_badge_keeps_ajax_contract(self):
        session = self.client.session
        session["cart"] = {"variant-1": 27}
        session.save()
        response = self.client.get("/en/")
        self.assertContains(response, "data-cart-count", count=2)
        self.assertEqual(
            len(re.findall(rb"data-cart-count[^>]*>\s*27\s*</span>", response.content)),
            2,
        )

    def test_drawer_has_modal_relationship_focus_and_logical_side_hooks(self):
        response = self.client.get("/en/")
        self.assertContains(response, 'role="dialog"', count=2)
        self.assertContains(response, 'aria-modal="true"', count=2)
        self.assertContains(response, 'aria-labelledby="mobileDrawerTitle"')
        self.assertContains(response, 'aria-label="Close navigation"')
        self.assertContains(response, "data-mobile-drawer-backdrop")
        css = self.source("theme/static_src/src/styles.css")
        self.assertIn("width: min(22rem, 88vw)", css)
        self.assertIn("inset-inline-start: 0", css)
        self.assertIn('[dir="rtl"] .mobile-drawer', css)
        self.assertIn("height: 100dvh", css)

    def test_mobile_search_reuses_shared_localized_contract(self):
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        self.assertContains(english, 'data-search-endpoint="/en/products/search-suggestions/"', count=2)
        self.assertContains(persian, 'data-search-endpoint="/fa/products/search-suggestions/"', count=2)
        self.assertContains(english, 'id="mobileHeaderSearchInput"')
        self.assertContains(english, 'aria-controls="mobileHeaderSearchSuggestions"')
        mobile_search = self.source("templates/components/navbar/mobile_search.html")
        drawer = self.source("templates/components/navbar/mobile_drawer.html")
        self.assertEqual(mobile_search.count('include "components/navbar/searchbar.html"'), 1)
        self.assertNotIn('include "components/navbar/searchbar.html"', drawer)
        self.assertIn("data-mobile-search-close", mobile_search)

    def test_anonymous_authenticated_staff_and_logout_paths_are_preserved(self):
        anonymous = self.client.get("/en/")
        self.assertContains(anonymous, 'href="/en/accounts/login/"')
        self.assertContains(anonymous, 'href="/en/accounts/register/"')

        user = User.objects.create_user("member", email="member@example.com", password="test-pass")
        self.client.force_login(user)
        authenticated = self.client.get("/en/")
        self.assertContains(authenticated, "mobileAccountTitle")
        self.assertContains(authenticated, 'href="/en/accounts/"')
        self.assertContains(authenticated, 'data-logout-trigger', count=2)
        self.assertContains(authenticated, 'data-logout-dialog')
        self.assertContains(authenticated, 'data-logout-form')
        self.assertContains(authenticated, 'method="post" action="/en/accounts/logout/"', count=1)
        self.assertContains(authenticated, 'csrfmiddlewaretoken')
        self.assertNotContains(authenticated, ">Dashboard</a>", html=False)

        call_command("seed_dashboard_roles", verbosity=0)
        user.groups.add(Group.objects.get(name="Product Manager"))
        self.assertContains(self.client.get("/en/"), ">Dashboard</a>", html=False)

    def test_unread_notification_is_one_drawer_shortcut_not_mobile_preview(self):
        user = User.objects.create_user("member", password="test-pass")
        Notification.objects.create(
            user=user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_CONFIRMED,
        )
        self.client.force_login(user)
        response = self.client.get("/en/")
        self.assertContains(response, "data-mobile-notification-count", count=1)
        self.assertContains(response, 'href="/en/notifications/"')
        mobile_source = self.source("templates/components/navbar/mobile_nav.html")
        self.assertNotIn("data-notification-menu", mobile_source)

    def test_language_theme_and_translated_labels_live_in_drawer(self):
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        self.assertContains(english, "فارسی")
        self.assertContains(persian, "EN")
        drawer = self.source("templates/components/navbar/mobile_drawer.html")
        self.assertIn("long_label=True", drawer)
        self.assertIn("data-theme-toggle", drawer)
        self.assertIn('data-lucide="moon"', drawer)
        self.assertIn('data-lucide="sun"', drawer)

    def test_javascript_has_focus_scroll_escape_return_and_overlay_coordination(self):
        source = self.source("static/js/navbar.js")
        for contract in (
            "containFocus", ".inert = true", "sf-scroll-locked",
            'event.key === "Escape"', "restoreFocus", "closeButton.focus()",
            "input.focus()", "storefront:header-overlay-open",
            "layer.contains(event.detail.source)", "data-mobile-disclosure",
        ):
            self.assertIn(contract, source)

    def test_desktop_v1b_contract_remains_present(self):
        response = self.client.get("/en/")
        self.assertContains(response, "data-shop-trigger")
        self.assertContains(response, 'aria-controls="desktopShopMegaMenu"')
        css = self.source("theme/static_src/src/styles.css")
        self.assertIn(".desktop-header { height: 4.625rem; }", css)
        self.assertIn("width: min(62rem, 100%)", css)


class MobileNavigationPayloadReuseTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.category = Category.objects.create(name="Floors", slug="floors")
        product_type = ProductType.objects.create(code="ceramic-tile", name="Tile & Ceramic")
        brand = Brand.objects.create(name="Atlas", slug="atlas")
        product = Product.objects.create(name="Tile", slug="tile", sku="TILE-1", product_type=product_type, brand=brand)
        product.categories.add(self.category)

    def test_mobile_shop_uses_prepared_payload_and_renders_with_zero_queries(self):
        request = self.factory.get("/en/")
        request.user = AnonymousUser()
        with override("en"):
            payload = build_storefront_navigation(request)
            with CaptureQueriesContext(connection) as captured:
                rendered = render_to_string(
                    "components/navbar/mobile_drawer.html",
                    {
                        "request": request,
                        "user": request.user,
                        "cart_count": 0,
                        "notification_unread_count": 0,
                        "storefront_navigation": payload,
                    },
                )
        self.assertEqual(len(captured), 0)
        for section_id in ("mobileShopSection1", "mobileShopSection2", "mobileShopSection3"):
            self.assertIn(section_id, rendered)
        self.assertIn("Categories", rendered)
        self.assertIn("Shop by Space", rendered)
        self.assertIn("Brands", rendered)
        self.assertIn("/en/shop/?categories=floors", rendered)
        self.assertIn("brand=atlas", rendered)
        self.assertIn('aria-expanded="false"', rendered)
        self.assertIn('aria-controls="mobileShopDisclosure"', rendered)
