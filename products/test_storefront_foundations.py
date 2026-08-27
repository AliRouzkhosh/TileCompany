from pathlib import Path

from django.conf import settings
from django.test import TestCase


class StorefrontFoundationTests(TestCase):
    def source(self, relative_path):
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_global_shell_keeps_language_direction_and_renders_shared_brand(self):
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")

        self.assertContains(english, '<html lang="en" dir="ltr">', html=False)
        self.assertContains(persian, '<html lang="fa" dir="rtl">', html=False)
        self.assertContains(english, "data-store-brand", count=4)
        self.assertContains(persian, "data-store-brand", count=4)
        self.assertContains(english, 'href="/en/"', count=4)
        self.assertContains(persian, 'href="/fa/"', count=4)
        self.assertContains(english, 'aria-label="TileStore home"', count=4)
        self.assertContains(persian, 'aria-label="صفحهٔ اصلی تایل‌استور"', count=4)

    def test_brand_partial_is_geometric_and_has_no_emoji_mark(self):
        source = self.source("templates/components/brand.html")

        self.assertIn("<svg", source)
        self.assertIn("data-store-brand", source)
        self.assertNotIn("🧱", source)

    def test_semantic_tokens_container_and_primitives_are_declared(self):
        source = self.source("theme/static_src/src/styles.css")

        for token in (
            "--color-page", "--color-surface", "--color-surface-subtle",
            "--color-surface-raised", "--color-text-primary",
            "--color-text-secondary", "--color-text-muted", "--color-border",
            "--color-border-strong", "--color-brand", "--color-brand-hover",
            "--color-brand-active", "--color-brand-subtle", "--color-success",
            "--color-warning", "--color-error", "--color-info",
            "--radius-chip", "--radius-control", "--radius-surface",
            "--radius-feature",
        ):
            self.assertIn(token, source)

        self.assertIn(".store-container", source)
        self.assertIn("max-width: 88rem", source)
        for primitive in (
            ".sf-button--primary", ".sf-button--secondary",
            ".sf-button--quiet", ".sf-icon-button", ".sf-surface",
            ".sf-surface-raised",
        ):
            self.assertIn(primitive, source)

    def test_official_inter_variable_font_is_declared_for_required_weights(self):
        css = self.source("static/css/theme.css")
        font = Path(settings.BASE_DIR) / "static/fonts/inter/InterVariable.woff2"

        self.assertTrue(font.is_file())
        self.assertIn('../fonts/inter/InterVariable.woff2', css)
        self.assertIn("font-weight: 100 900", css)
        self.assertIn('font-family: "Inter", system-ui, sans-serif', css)
        self.assertIn('font-family: "Vazirmatn", system-ui, sans-serif', css)

    def test_script_ownership_is_classic_and_each_global_script_loads_once(self):
        response = self.client.get("/en/")
        main_source = self.source("static/js/main.js")

        self.assertNotIn('import "./', main_source)
        for script in (
            "navbar.js", "search.js", "swiper.js", "main.js", "drawer.js",
            "product_action.js", "wishlist.js", "toast.js", "theme-toggle.js",
        ):
            self.assertContains(response, f"js/{script}", count=1)

        cart = self.client.get("/en/orders/cart/")
        self.assertContains(cart, "js/cart.js", count=1)
