from pathlib import Path
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.utils.translation import override


class StorefrontNavbarFinalQATests(TestCase):
    def source(self, relative_path):
        return (Path(__file__).resolve().parent.parent / relative_path).read_text(encoding="utf-8")

    def test_shop_open_state_remains_navigation_weight_and_focus_ring_is_preserved(self):
        css = self.source("theme/static_src/src/styles.css")
        self.assertIn('.desktop-nav-link[aria-expanded="true"]', css)
        self.assertIn("color-mix(in srgb, var(--color-brand-subtle) 58%, transparent)", css)
        self.assertNotIn(
            '.desktop-nav-link[aria-expanded="true"] {\n    background: var(--color-brand);',
            css,
        )
        self.assertIn(":focus-visible", css)

    def test_mega_menu_uses_compact_represented_content_rhythm(self):
        css = self.source("theme/static_src/src/styles.css")
        template = self.source("templates/components/navbar/shop_mega_menu.html")
        self.assertIn("min-height: 2.25rem", css)
        self.assertIn('class="mb-2 ', template)
        self.assertIn("grid grid-cols-3 gap-10 p-8", template)
        self.assertNotIn("View all products", template)
        self.assertEqual(template.count("{% for section in storefront_navigation.sections %}"), 1)

    def test_1023_1024_shell_contract_and_responsive_reset_are_explicit(self):
        desktop = self.source("templates/components/navbar/desktop_nav.html")
        mobile = self.source("templates/components/navbar/mobile_nav.html")
        javascript = self.source("static/js/navbar.js")
        self.assertIn("hidden", desktop)
        self.assertIn("lg:block", desktop)
        self.assertIn("lg:hidden", mobile)
        for contract in (
            'window.matchMedia("(min-width: 64rem)")',
            'announceHeaderOverlay("responsive-reset"',
            "releaseMobileBackground()",
            "document.activeElement.blur()",
            'button.setAttribute("aria-expanded", "false")',
        ):
            self.assertIn(contract, javascript)

    def test_pointer_click_and_keyboard_focus_paths_remain_distinct(self):
        javascript = self.source("static/js/navbar.js")
        self.assertIn('triggerArea.addEventListener("pointerenter"', javascript)
        self.assertIn('triggerArea.addEventListener("pointerleave"', javascript)
        self.assertIn('trigger.addEventListener("click"', javascript)
        self.assertIn("triggerArea.contains(event.target)", javascript)
        self.assertIn('disclosure.addEventListener("focusin"', javascript)

    def test_long_mobile_labels_and_large_badges_render_without_new_contracts(self):
        request = RequestFactory().get("/en/")
        user = SimpleNamespace(
            is_authenticated=True,
            is_staff=False,
            username="customer-with-an-intentionally-long-account-name",
            email="an-intentionally-long-email-address@example-storefront.test",
        )
        long_label = "A deliberately long represented navigation label that must wrap safely"
        payload = {
            "sections": [
                {
                    "code": "categories",
                    "label": "Categories",
                    "items": [{"label": long_label, "url": "/en/shop/category/long/", "children": []}],
                },
                {"code": "shop-by-space", "label": "Shop by Space", "items": [{"label": long_label, "url": "/en/shop/?product_type=ceramic-tile&attr_demo-application-area=long"}]},
                {"code": "brands", "label": "Brands", "items": [{"label": long_label, "url": "/en/shop/?brand=long"}]},
            ],
            "view_all_url": "/en/shop/",
        }
        with override("en"):
            drawer = render_to_string(
                "components/navbar/mobile_drawer.html",
                {
                    "request": request,
                    "user": user,
                    "storefront_navigation": payload,
                    "notification_unread_count": 100,
                    "cart_count": 0,
                },
            )
            header = render_to_string(
                "components/navbar/mobile_nav.html",
                {"request": request, "user": user, "cart_count": 123},
            )
        self.assertEqual(drawer.count(long_label), 3)
        self.assertIn("99+", drawer)
        self.assertIn("data-cart-count", header)
        self.assertIn("123", header)
        css = self.source("theme/static_src/src/styles.css")
        self.assertIn("overflow-wrap: anywhere", css)
        self.assertIn("--search-suggestions-max-height: calc(100dvh - 13.5rem)", css)
        search = self.source("templates/components/navbar/searchbar.html")
        self.assertIn("max-h-[var(--search-suggestions-max-height,70vh)]", search)
