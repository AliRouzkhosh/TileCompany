import re
from pathlib import Path

from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.translation import override

from .models import Category, Product, ProductType
from .storefront_navigation import build_storefront_navigation


class StorefrontFooterRedesignTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.category = Category.objects.create(
            name="Large Format Surfaces",
            name_fa="سطوح اسلب و بزرگ‌مقیاس",
            slug="large-format-surfaces",
            is_active=True,
            show_in_navigation=True,
            sort_order=1,
        )
        product_type = ProductType.objects.create(code="footer-type", name="Footer type")
        product = Product.objects.create(
            name="Footer product", slug="footer-product", sku="FOOTER-1", product_type=product_type,
        )
        product.categories.add(self.category)

    def source(self, relative_path):
        return (Path(__file__).resolve().parent.parent / relative_path).read_text(encoding="utf-8")

    def payload(self, language="en"):
        with override(language):
            return build_storefront_navigation(self.factory.get(f"/{language}/"))

    def render_footer(self, language="en", payload=None):
        with override(language):
            return render_to_string(
                "components/footer.html",
                {"storefront_navigation": payload or self.payload(language)},
            )

    def test_footer_uses_shared_brand_and_has_no_placeholder_links(self):
        source = self.source("templates/components/footer.html")
        rendered = self.render_footer()
        self.assertIn('{% include "components/brand.html" %}', source)
        self.assertNotIn('href="#"', source)
        self.assertNotIn('href="#"', rendered)
        self.assertNotRegex(rendered, r'href="\s*"')

    def test_only_real_named_destinations_are_rendered(self):
        rendered = self.render_footer()
        for url in ("/en/shop/", "/en/articles/", "/en/support/", "/en/support/help/"):
            self.assertIn(f'href="{url}"', rendered)
        self.assertIn('href="/en/shop/?categories=large-format-surfaces"', rendered)

    def test_generic_navigation_categories_are_localized_and_bounded(self):
        english = self.render_footer("en")
        persian = self.render_footer("fa")
        self.assertIn("Large Format Surfaces", english)
        self.assertIn("سطوح اسلب و بزرگ‌مقیاس", persian)
        source = self.source("templates/components/footer.html")
        self.assertIn('section.code == "categories"', source)
        self.assertIn('section.items|slice:":6"', source)
        self.assertNotIn(".children", source)

    def test_mobile_native_disclosures_default_closed_and_desktop_columns_are_visible(self):
        source = self.source("templates/components/footer.html")
        rendered = self.render_footer()
        self.assertEqual(rendered.count("<details"), 3)
        self.assertEqual(rendered.count("<summary>"), 3)
        self.assertNotRegex(rendered, r"<details[^>]+\sopen(?:\s|>)")
        self.assertIn("data-footer-mobile-navigation", source)
        self.assertIn("data-footer-desktop-navigation", source)
        self.assertIn("lg:hidden", source)
        self.assertIn("lg:grid", source)

    def test_en_fa_labels_and_restrained_brand_copy_render(self):
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        for label in ("Shop", "Explore", "Support", "All Products", "Help articles"):
            self.assertContains(english, label)
        for label in ("فروشگاه", "مطالب", "پشتیبانی"):
            self.assertContains(persian, label)
        source = self.source("templates/components/footer.html")
        self.assertIn("Discover tiles for floors, walls, and every space", source)

    def test_unsupported_claims_social_contact_and_legal_placeholders_are_absent(self):
        source = self.source("templates/components/footer.html")
        lowered = source.lower()
        for forbidden in (
            "europe's leading manufacturers",
            "european sourcing",
            "free delivery",
            "guaranteed quality",
            "instagram",
            "telegram",
            "mailto:",
            "tel:",
            "cookies",
            "sustainability",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_footer_has_no_emoji_or_raw_legacy_palette(self):
        source = self.source("templates/components/footer.html")
        self.assertNotRegex(source, "[😀-🙏🌀-🫿]")
        for raw_palette in ("stone-", "amber-", "gray-", "emerald-"):
            self.assertNotIn(raw_palette, source)

    def test_footer_template_performs_zero_orm_queries_with_prepared_payload(self):
        payload = self.payload("en")
        with override("en"), CaptureQueriesContext(connection) as captured:
            rendered = render_to_string(
                "components/footer.html",
                {"storefront_navigation": payload},
            )
        self.assertEqual(len(captured), 0)
        self.assertIn("Large Format Surfaces", rendered)

    def test_year_is_dynamic_and_footer_is_semantic(self):
        source = self.source("templates/components/footer.html")
        self.assertIn("<footer", source)
        self.assertIn('{% now "Y" %}', source)
        self.assertNotRegex(source, re.compile(r"©\s*20\d{2}"))
