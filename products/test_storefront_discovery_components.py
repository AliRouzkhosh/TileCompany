from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import re

from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus, BlogCategory
from products.catalog_presentation import prepare_product_card_presentations
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantPrice,
)
from products.selectors import get_featured_products, get_home_categories


class StorefrontDiscoveryComponentTests(TestCase):
    def source(self, relative_path):
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def make_product(self, *, name="Gallery Surface", name_fa="سطح گالری", image=""):
        product_type = ProductType.objects.create(
            code=f"surface-{ProductType.objects.count()}", name="Surface"
        )
        product = Product.objects.create(
            product_type=product_type,
            name=name,
            name_fa=name_fa,
            slug=f"surface-{Product.objects.count()}",
            sku=f"SURFACE-{Product.objects.count()}",
            image=image,
            is_featured=True,
        )
        variant = ProductVariant.objects.create(
            product=product,
            sku=f"{product.sku}-A",
            price_unit=ProductVariant.PriceUnit.BOX,
            is_active=True,
            stock=12,
        )
        VariantPrice.objects.create(
            variant=variant,
            currency=Currency.USD,
            amount=Decimal("42.00"),
            compare_at_amount=Decimal("50.00"),
        )
        return product, variant

    def add_generic_card_attribute(self, product):
        definition = AttributeDefinition.objects.create(
            code="surface_kind", name="Surface kind", name_fa="نوع سطح", data_type="option"
        )
        option = AttributeOption.objects.create(
            definition=definition,
            code="porcelain",
            name="Porcelain",
            name_fa="پرسلانی",
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=product.product_type,
            definition=definition,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            show_on_product_card=True,
            show_on_storefront=True,
            sort_order=1,
        )
        ProductAttributeValue.objects.create(
            product=product, binding=binding, option_value=option
        )

    def test_hero_is_localized_uses_named_actions_and_has_image_fallback_contract(self):
        tiles = Category.objects.create(name="Tiles & Ceramics", slug="tiles-ceramics", sort_order=1)
        tools = Category.objects.create(name="Tile Tools", slug="tile-tools", sort_order=2)
        self.make_product()[0].categories.add(tiles)
        self.make_product()[0].categories.add(tools)
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")

        self.assertContains(english, "Materials for every surface and every project.")
        self.assertContains(english, 'href="/en/shop/?categories=tiles-ceramics"')
        self.assertContains(english, 'href="/en/shop/?categories=tile-tools"')
        self.assertContains(persian, 'href="/fa/shop/?categories=tiles-ceramics"')
        self.assertContains(persian, 'href="/fa/shop/?categories=tile-tools"')
        self.assertContains(english, "data-home-hero")
        self.assertContains(english, "data-hero-media")
        self.assertContains(english, "Tile showroom image coming soon")
        self.assertNotIn("🧱", self.source("products/templates/products/components/hero.html"))

    def test_hero_prefers_an_existing_featured_product_image(self):
        self.make_product(image="products/existing-showroom.jpg")
        response = self.client.get("/en/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/media/products/existing-showroom.jpg')
        self.assertNotContains(response, "Tile showroom image coming soon")

    def test_home_category_selector_is_bounded_truthful_deterministic_and_query_safe(self):
        eligible = Category.objects.create(
            name="Eligible", name_fa="مجاز", slug="eligible", sort_order=2
        )
        first = Category.objects.create(
            name="First", name_fa="اول", slug="first", sort_order=1
        )
        inactive = Category.objects.create(
            name="Inactive", slug="inactive", is_active=False
        )
        hidden = Category.objects.create(
            name="Hidden", slug="hidden", show_in_navigation=False
        )
        Category.objects.create(name="Child", slug="child", parent=eligible)
        product, _ = self.make_product(image="products/category-room.jpg")
        product.categories.add(eligible)

        with self.assertNumQueries(1):
            categories = get_home_categories(limit=99)

        self.assertEqual([row.slug for row in categories[:2]], ["first", "eligible"])
        self.assertNotIn(inactive.slug, [row.slug for row in categories])
        self.assertNotIn(hidden.slug, [row.slug for row in categories])
        self.assertNotIn("child", [row.slug for row in categories])
        self.assertLessEqual(len(categories), 8)
        selected = next(row for row in categories if row.slug == "eligible")
        self.assertEqual(selected.name_fa, "مجاز")
        self.assertEqual(selected.product_count, 1)
        self.assertEqual(selected.image_url, "/media/products/category-room.jpg")

    def test_category_card_is_visual_localized_and_contains_no_emoji(self):
        category = Category.objects.create(
            name="Bathrooms", name_fa="حمام", slug="bathrooms"
        )
        card = get_home_categories()[0]
        with translation.override("fa"):
            with self.assertNumQueries(0):
                rendered = render_to_string(
                    "products/components/category_card.html", {"category": card}
                )

        self.assertIn("حمام", rendered)
        self.assertIn("category-discovery-card", rendered)
        self.assertIn("h-full min-h-72 w-full", rendered)
        self.assertIn("data-category-media", rendered)
        self.assertIn('/fa/shop/category/bathrooms/', rendered)
        self.assertNotIn("🧱", rendered)

    def test_category_card_geometry_is_stable_for_all_image_states(self):
        rendered_states = []
        for index, image_url in enumerate((
            "/media/storefront/categories/custom.jpg",
            "/media/products/representative.jpg",
            "",
        )):
            category = type("CategoryCard", (), {
                "slug": f"generic-{index}",
                "name": f"Generic {index}",
                "name_fa": "دسته عمومی",
                "product_count": index,
                "image_url": image_url,
            })()
            with self.assertNumQueries(0):
                rendered_states.append(render_to_string(
                    "products/components/category_card.html", {"category": category}
                ))

        for rendered in rendered_states:
            self.assertIn("category-discovery-card", rendered)
            self.assertIn("h-full min-h-72 w-full", rendered)
            self.assertIn("data-category-media", rendered)
        self.assertIn("custom.jpg", rendered_states[0])
        self.assertIn("representative.jpg", rendered_states[1])
        self.assertIn("data-category-image-fallback", rendered_states[2])

    def test_product_card_preserves_generic_contract_and_has_one_sale_zone(self):
        product, variant = self.make_product()
        self.add_generic_card_attribute(product)
        prepared = list(get_featured_products(Currency.USD))
        prepare_product_card_presentations(
            prepared, language="en", currency=Currency.USD
        )
        card_product = prepared[0]
        card_product.target_variant_id = variant.pk

        rendered = render_to_string(
            "products/components/product_card.html",
            {
                "product": card_product,
                "storefront_currency": Currency.USD,
                "card_mode": "listing",
                "query_string": "sort=newest",
            },
        )

        self.assertEqual(rendered.count("data-sale-badge"), 1)
        self.assertEqual(rendered.count("data-product-badge-zone"), 1)
        self.assertIn("Gallery Surface", rendered)
        self.assertIn("Porcelain", rendered)
        self.assertIn("$42.00", rendered)
        self.assertIn("$50.00", rendered)
        self.assertIn("Per Box", rendered)
        self.assertIn(f'variant={variant.pk}" hx-boost="false"', rendered)
        self.assertNotIn("sort=newest", rendered)
        self.assertIn("data-product-image-fallback", rendered)
        self.assertNotIn("View Details", rendered)
        self.assertNotIn("<form", rendered)
        self.assertNotIn("<button", rendered)

    def test_product_card_localizes_name_attribute_price_and_unit(self):
        product, _ = self.make_product()
        self.add_generic_card_attribute(product)
        prepared = list(get_featured_products(Currency.USD))
        prepare_product_card_presentations(
            prepared, language="fa", currency=Currency.USD
        )
        with translation.override("fa"):
            rendered = render_to_string(
                "products/components/product_card.html",
                {"product": prepared[0], "storefront_currency": Currency.USD},
            )

        self.assertIn("سطح گالری", rendered)
        self.assertIn("پرسلانی", rendered)
        self.assertIn("۴۲٫۰۰", rendered)
        self.assertIn("به‌ازای هر جعبه", rendered)

    def test_product_media_geometry_is_identical_with_and_without_image(self):
        self.make_product(name="Image Product", image="products/image.jpg")
        _, fallback_variant = self.make_product(name="Fallback Product", image="")
        VariantPrice.objects.filter(variant=fallback_variant).update(compare_at_amount=None)
        prepared = list(get_featured_products(Currency.USD))
        prepare_product_card_presentations(
            prepared, language="en", currency=Currency.USD
        )
        by_name = {product.name: product for product in prepared}

        rendered_image = render_to_string(
            "products/components/product_card.html",
            {"product": by_name["Image Product"], "storefront_currency": Currency.USD},
        )
        rendered_fallback = render_to_string(
            "products/components/product_card.html",
            {"product": by_name["Fallback Product"], "storefront_currency": Currency.USD},
        )
        media_class = re.compile(r'<div class="([^"]+)" data-product-media>')

        self.assertEqual(
            media_class.search(rendered_image).group(1),
            media_class.search(rendered_fallback).group(1),
        )
        for rendered in (rendered_image, rendered_fallback):
            self.assertIn("aspect-[4/5] w-full shrink-0", rendered)
            self.assertIn("product-discovery-card group relative flex h-full w-full", rendered)
            self.assertEqual(rendered.count("data-product-media"), 1)
        self.assertIn("data-sale-badge", rendered_image)
        self.assertNotIn("data-sale-badge", rendered_fallback)
        self.assertIn("home-media-fallback flex h-full w-full", rendered_fallback)

        with translation.override("fa"):
            localized_fallback = render_to_string(
                "products/components/product_card.html",
                {"product": by_name["Fallback Product"], "storefront_currency": Currency.USD},
            )
        self.assertNotIn("Image coming soon", localized_fallback)

    def test_article_card_localizes_supported_metadata_and_fallback(self):
        category = BlogCategory.objects.create(
            name="Inspiration", name_fa="الهام", slug="inspiration"
        )
        article = Article.objects.create(
            title="A calmer room",
            title_fa="فضایی آرام‌تر",
            summary="A practical guide.",
            summary_fa="راهنمایی کاربردی.",
            content="Body",
            content_fa="متن",
            slug="calmer-room",
            category=category,
            status=ArticleStatus.PUBLISHED,
            published_at=timezone.now() - timedelta(days=1),
        )
        with translation.override("fa"):
            rendered = render_to_string(
                "products/components/article_card.html", {"article": article}
            )

        self.assertIn("فضایی آرام‌تر", rendered)
        self.assertIn("الهام", rendered)
        self.assertIn("data-article-image-fallback", rendered)
        self.assertIn("aspect-[16/10] w-full shrink-0", rendered)
        self.assertIn("home-media-fallback flex h-full w-full", rendered)
        self.assertNotIn("🧱", rendered)

    def test_shared_consumers_keep_one_product_card_and_sources_are_domain_neutral(self):
        consumers = (
            "products/templates/products/home.html",
            "products/templates/products/components/catalog_workspace.html",
            "products/templates/products/components/related_products.html",
            "blog/templates/blog/components/related_products.html",
        )
        for path in consumers:
            self.assertIn(
                'products/components/product_card.html', self.source(path), path
            )

        shared_source = "\n".join(
            self.source(path)
            for path in (
                "products/templates/products/components/product_card.html",
                "products/templates/products/components/category_card.html",
                "products/templates/products/components/hero.html",
                "products/templates/products/components/article_card.html",
            )
        )
        for forbidden in ("ProductType", "length_mm", "width_mm", "thickness_mm"):
            self.assertNotIn(forbidden, shared_source)

    def test_discovery_swiper_and_responsive_hooks_are_scoped(self):
        home = self.source("products/templates/products/home.html")
        swiper = self.source("static/js/swiper.js")
        styles = self.source("theme/static_src/src/styles.css")

        self.assertIn("data-home-discovery", home)
        self.assertIn("swiper_controls.html", home)
        self.assertIn('slidesPerView: "auto"', swiper)
        self.assertIn("1024: { slidesPerView: 3.5", swiper)
        self.assertIn("1280: { slidesPerView: 4", swiper)
        self.assertIn(".home-discovery", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
