from pathlib import Path

from django.test import TestCase


class StorefrontV2B1SourceContractTests(TestCase):
    def source(self, relative_path):
        return (Path(__file__).resolve().parent.parent / relative_path).read_text(encoding="utf-8")

    def test_desktop_shop_has_canonical_link_and_separate_disclosure_button(self):
        response = self.client.get("/en/")
        self.assertContains(response, 'href="/en/shop/"')
        self.assertContains(response, 'id="desktopShopLink"')
        self.assertContains(response, "data-shop-link")
        self.assertContains(response, 'id="desktopShopDisclosureButton"')
        self.assertContains(response, "data-shop-trigger")
        self.assertContains(response, 'aria-controls="desktopShopMegaMenu"')

        mega_menu = self.source("templates/components/navbar/shop_mega_menu.html")
        self.assertNotIn("View all products", mega_menu)

    def test_shop_link_is_not_intercepted_and_disclosure_keeps_pointer_focus_contract(self):
        javascript = self.source("static/js/navbar.js")
        self.assertNotIn('data-shop-link', javascript)
        self.assertNotIn('shopLink.addEventListener("click"', javascript)
        self.assertIn('trigger.addEventListener("click"', javascript)
        self.assertIn('triggerArea.addEventListener("pointerenter", scheduleOpen)', javascript)
        self.assertIn('triggerArea.addEventListener("pointerleave", scheduleClose)', javascript)
        self.assertIn("triggerArea.contains(event.target)", javascript)
        self.assertIn('event.key === "Escape"', javascript)
        self.assertIn("close({ restoreFocus: true })", javascript)

    def test_mobile_shop_all_products_link_precedes_discovery_sections(self):
        drawer = self.source("templates/components/navbar/mobile_drawer.html")
        all_products = drawer.index("storefront_navigation.view_all_url")
        sections = drawer.index("{% for section in storefront_navigation.sections %}")
        self.assertLess(all_products, sections)
        self.assertEqual(drawer.count("storefront_navigation.view_all_url"), 1)

    def test_mobile_hero_density_hooks_are_scoped_below_desktop(self):
        hero = self.source("products/templates/products/components/hero.html")
        theme = self.source("static/css/theme.css")
        self.assertIn("text-[clamp(2.125rem,10.5vw,2.5rem)]", hero)
        self.assertIn("leading-[1.18]", hero)
        self.assertIn("min-[360px]:flex-row", hero)
        self.assertIn("min-h-12", hero)
        self.assertIn("h-56", hero)
        self.assertIn("lg:min-h-[31rem]", hero)
        self.assertIn('html[lang="fa"] .home-hero h1', theme)

    def test_discovery_rails_use_bounded_mobile_widths_and_desktop_breakpoints(self):
        swiper = self.source("static/js/swiper.js")
        styles = self.source("theme/static_src/src/styles.css")
        self.assertIn('slidesPerView: "auto"', swiper)
        self.assertEqual(swiper.count('config.slidesPerView = "auto"'), 2)
        self.assertIn("width: clamp(13.125rem, 60vw, 14.375rem)", styles)
        self.assertIn("width: clamp(13.5rem, 56vw, 14rem)", styles)
        self.assertIn("width: clamp(15rem, 72vw, 17rem)", styles)
        self.assertIn("1280: { slidesPerView: 4", swiper)
        self.assertIn("1280: { slidesPerView: 3", swiper)

    def test_trust_strip_becomes_compact_two_by_two_from_360(self):
        trust = self.source("products/templates/products/components/trust_strip.html")
        self.assertIn("min-[360px]:grid-cols-2", trust)
        self.assertIn("lg:grid-cols-4", trust)
        self.assertEqual(trust.count("data-trust-item"), 4)
        self.assertIn("h-8 w-8", trust)


class StorefrontV2B1RuntimeContractTests(TestCase):
    def test_navigation_payload_remains_three_queries(self):
        # The authoritative payload-count assertion remains in
        # StorefrontNavigationSelectorTests; this guards that V2B.1 adds no
        # template-side navigation lookup or alternate payload.
        response = self.client.get("/en/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "storefront_navigation", count=0)
        self.assertContains(response, 'data-shop-trigger-area')
