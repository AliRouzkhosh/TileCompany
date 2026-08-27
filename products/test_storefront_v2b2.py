from pathlib import Path

from django.test import TestCase


class StorefrontV2B2SourceContractTests(TestCase):
    def source(self, relative_path):
        return (Path(__file__).resolve().parent.parent / relative_path).read_text(encoding="utf-8")

    def test_sale_surface_is_the_final_paint_boundary(self):
        home = self.source("products/templates/products/home.html")
        self.assertIn(
            'home-section--sale overflow-hidden rounded-feature',
            home,
        )

    def test_sale_rail_expansion_is_container_relative_not_viewport_relative(self):
        styles = self.source("theme/static_src/src/styles.css")
        sale_rule = styles.split(".home-section--sale .product-swiper {", 1)[1].split("}", 1)[0]
        self.assertIn("margin-inline: -1rem !important", sale_rule)
        self.assertIn("padding-inline: 0.25rem !important", sale_rule)
        for escaped_geometry in ("100vw", "calc(100% + 2rem)", "store-full-bleed"):
            self.assertNotIn(escaped_geometry, sale_rule)

    def test_featured_and_sale_share_one_mobile_product_slide_width(self):
        home = self.source("products/templates/products/home.html")
        styles = self.source("theme/static_src/src/styles.css")
        self.assertIn("width: clamp(13.5rem, 56vw, 14rem) !important", styles)
        self.assertNotIn("home-section--sale .swiper-slide", styles)
        self.assertEqual(home.count('class="swiper product-swiper"'), 2)
        self.assertEqual(home.count('products/components/product_card.html'), 2)

    def test_sale_edge_treatment_is_logical_for_ltr_and_rtl(self):
        styles = self.source("theme/static_src/src/styles.css")
        sale_rule = styles.split(".home-section--sale .product-swiper {", 1)[1].split("}", 1)[0]
        self.assertIn("margin-inline", sale_rule)
        self.assertIn("padding-inline", sale_rule)
        self.assertNotIn("margin-left", sale_rule)
        self.assertNotIn("margin-right", sale_rule)

    def test_product_card_presentation_contract_is_not_forked(self):
        home = self.source("products/templates/products/home.html")
        self.assertNotIn("sale_product_card", home)
        self.assertIn('with product=product card_mode="home"', home)
        self.assertIn('with product=product %}', home)

    def test_mobile_section_header_and_narrative_gaps_remain_deliberate(self):
        header = self.source("products/templates/products/components/section_header.html")
        styles = self.source("theme/static_src/src/styles.css")
        self.assertIn("mb-6", header)
        self.assertIn("gap-2", header)
        self.assertIn("sm:mb-8", header)
        self.assertIn('.home-discovery > [data-home-section="sale"]', styles)
        self.assertIn("margin-top: 3rem", styles)
        self.assertIn("margin-top: 2.25rem", styles)

