from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus
from blog.selectors import get_latest_articles
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    HomepageMerchandisingMedia,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantPrice,
)
from products.selectors import (
    get_home_application_areas,
    get_home_catalog_categories,
    get_home_categories,
    get_home_merchandising_products,
)


class StorefrontHomepageCompositionTests(TestCase):
    def source(self, relative_path):
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def make_product(self, *, featured=True, sale=True, unit=ProductVariant.PriceUnit.BOX):
        product_type = ProductType.objects.create(
            code=f"surface-{ProductType.objects.count()}",
            name="Surface",
        )
        product = Product.objects.create(
            product_type=product_type,
            name=f"Collection {Product.objects.count()}",
            name_fa="مجموعه منتخب",
            slug=f"collection-{Product.objects.count()}",
            sku=f"COL-{Product.objects.count()}",
            image="products/collection.jpg",
            is_featured=featured,
        )
        variant = ProductVariant.objects.create(
            product=product,
            sku=f"{product.sku}-A",
            price_unit=unit,
            stock=4,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=variant,
            currency=Currency.USD,
            amount=Decimal("42.00"),
            compare_at_amount=Decimal("50.00") if sale else None,
        )
        return product

    def make_article(self, *, slug="calm-space", image="blog/feature.jpg", days=1):
        return Article.objects.create(
            title="A calmer material palette",
            title_fa="ترکیب متریال آرام‌تر",
            summary="A practical guide to balanced surfaces.",
            summary_fa="راهنمایی کاربردی برای سطوح متعادل.",
            content="Article body",
            content_fa="متن مقاله",
            slug=slug,
            cover_image=image,
            status=ArticleStatus.PUBLISHED,
            published_at=timezone.now() - timedelta(days=days),
        )

    def test_sections_render_in_approved_order(self):
        root = Category.objects.create(name="Spaces", slug="spaces")
        child = Category.objects.create(name="Walls", slug="walls", parent=root)
        product = self.make_product()
        product.categories.add(child)
        self.make_article()
        response = self.client.get("/en/")
        content = response.content.decode()
        expected = ("hero", "gateways", "categories", "featured", "editorial", "trust", "support")

        positions = [content.index(f'data-home-section="{section}"') for section in expected]
        self.assertEqual(positions, sorted(positions))

    def test_homepage_requests_all_eight_bounded_categories(self):
        for index in range(9):
            Category.objects.create(
                name=f"Department {index}",
                slug=f"department-{index}",
                sort_order=index,
            )

        with self.assertNumQueries(1):
            selected = get_home_categories(limit=8)
        response = self.client.get("/en/")

        self.assertEqual(len(selected), 8)
        self.assertEqual(response.context["root_categories"], ())
        self.assertNotContains(response, 'data-home-section="gateways"')

    def test_merchandising_media_requires_exactly_one_canonical_target(self):
        category = Category.objects.create(name="Media category", slug="media-category")
        brand = Brand.objects.create(name="Media brand", slug="media-brand")
        with self.assertRaises(ValidationError):
            HomepageMerchandisingMedia().full_clean()
        with self.assertRaises(ValidationError):
            HomepageMerchandisingMedia(category=category, brand=brand).full_clean()
        HomepageMerchandisingMedia(category=category).full_clean()

    def test_represented_root_and_child_discovery_is_descendant_aware(self):
        root = Category.objects.create(name="Ceramics", name_fa="سرامیک", slug="ceramics")
        child = Category.objects.create(name="Porcelain", name_fa="پرسلان", slug="porcelain", parent=root)
        unused = Category.objects.create(name="Unused", slug="unused", parent=root)
        product = self.make_product(sale=False)
        product.categories.add(child)

        roots, children = get_home_catalog_categories()

        self.assertEqual([category.slug for category in roots], ["ceramics"])
        self.assertEqual(roots[0].product_count, 1)
        self.assertEqual([category.slug for category in children], ["porcelain"])
        self.assertNotIn(unused.slug, [category.slug for category in children])
        response = self.client.get("/fa/")
        self.assertContains(response, "سرامیک")
        self.assertContains(response, "/fa/shop/?categories=ceramics")

    def test_home_application_area_uses_public_option_and_canonical_combined_url(self):
        product = self.make_product(sale=False)
        product_type = product.product_type
        product_type.code = "ceramic-tile"
        product_type.save(update_fields=["code"])
        definition = AttributeDefinition.objects.create(
            code="demo-application-area",
            name="Application Area",
            name_fa="فضای کاربرد",
            data_type=AttributeDefinition.DataType.OPTION,
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=product_type,
            definition=definition,
            scope=ProductTypeAttribute.Scope.PRODUCT,
            is_filterable=True,
            show_on_storefront=True,
        )
        kitchen = AttributeOption.objects.create(definition=definition, code="kitchen", name="Kitchen", name_fa="آشپزخانه")
        AttributeOption.objects.create(definition=definition, code="internal", name="Internal", presentation_metadata={"internal": True})
        ProductAttributeValue.objects.create(product=product, binding=binding, option_value=kitchen)

        areas = get_home_application_areas()

        self.assertEqual([area.code for area in areas], ["kitchen"])
        self.assertIn("product_type=ceramic-tile", areas[0].url)
        self.assertIn("attr_demo-application-area=kitchen", areas[0].url)
        self.assertEqual(self.client.get(areas[0].url).context["page_obj"].paginator.count, 1)

    def test_merchandising_caps_deduplicates_and_hides_small_offer_set(self):
        featured_sale = self.make_product(featured=True, sale=True)
        non_sale_featured = self.make_product(featured=True, sale=False)
        for _ in range(3):
            self.make_product(featured=False, sale=True)

        featured, offers = get_home_merchandising_products(Currency.USD)

        self.assertEqual([product.pk for product in featured], [non_sale_featured.pk, featured_sale.pk])
        self.assertEqual(len(offers), 3)
        self.assertFalse({product.pk for product in featured} & {product.pk for product in offers})
        self.assertNotContains(self.client.get("/en/"), 'data-home-section="sale"')

    def test_offers_link_uses_the_canonical_current_currency_sale_filter(self):
        source = self.source("products/templates/products/home.html")

        self.assertIn("{% url 'products:product_list' as sale_url %}", source)
        self.assertIn("link=sale_url|add:'?on_sale=1'", source)

    def test_space_card_veil_is_theme_aware(self):
        styles = self.source("theme/static_src/src/styles.css")

        light_rule = styles.split(".home-discovery-tile__veil {", 1)[1].split("}", 1)[0]
        self.assertIn("48%", light_rule)
        self.assertIn("transparent 68%", light_rule)
        self.assertIn("html.dark .home-space-card .home-discovery-tile__veil", styles)

    def test_homepage_new_copy_and_selling_unit_follow_active_locale(self):
        root = Category.objects.create(name="Tiles", name_fa="کاشی", slug="tiles")
        child = Category.objects.create(name="Porcelain", name_fa="پرسلان", slug="porcelain", parent=root)
        brand = Brand.objects.create(name="Atlas", name_fa="اطلس", slug="atlas")
        featured = self.make_product(sale=False, unit=ProductVariant.PriceUnit.UNIT)
        featured.product_type.code = "ceramic-tile"
        featured.product_type.save(update_fields=["code"])
        featured.brand = brand
        featured.save(update_fields=["brand"])
        featured.categories.add(child)
        sale_products = []
        for _ in range(4):
            sale = self.make_product(featured=False, sale=True)
            sale.categories.add(child)
            sale_products.append(sale)

        for product in (featured, *sale_products):
            variant = product.variants.get()
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.TOMAN,
                amount=Decimal("420000"),
                compare_at_amount=Decimal("500000") if product != featured else None,
            )

        definition = AttributeDefinition.objects.create(
            code="demo-application-area", name="Application Area", name_fa="فضای کاربرد",
            data_type=AttributeDefinition.DataType.OPTION,
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=featured.product_type, definition=definition,
            scope=ProductTypeAttribute.Scope.PRODUCT, is_filterable=True,
        )
        kitchen = AttributeOption.objects.create(
            definition=definition, code="kitchen", name="Kitchen", name_fa="آشپزخانه",
        )
        ProductAttributeValue.objects.create(product=featured, binding=binding, option_value=kitchen)

        english = self.client.get("/en/")
        persian = self.client.get("/fa/")

        self.assertEqual(persian.context["featured_products"][0].card_commerce.selling_unit_label, "به‌ازای هر واحد")

        for label in ("Shop by Category", "Shop by Space", "Shop by Brand", "Offers", "Explore further", "Per Unit"):
            self.assertContains(english, label)
            self.assertNotContains(persian, label)
        for label in ("خرید بر اساس دسته‌بندی", "خرید بر اساس فضا", "خرید بر اساس برند", "پیشنهادها", "بیشتر کاوش کنید", "به‌ازای هر واحد"):
            self.assertContains(persian, label)

    def test_featured_uses_shared_card_and_omits_cleanly_when_empty(self):
        empty = self.client.get("/en/")
        self.assertNotContains(empty, 'data-home-section="featured"')
        self.assertNotContains(empty, 'data-discovery-rail="featured"')

        self.make_product(sale=False)
        populated = self.client.get("/en/")
        self.assertContains(populated, 'data-home-section="featured"')
        self.assertContains(populated, 'data-product-card')
        self.assertNotContains(populated, "Best seller")
        self.assertNotContains(populated, "New arrival")

    def test_editorial_reuses_bounded_latest_article_payload_and_localizes(self):
        newest_without_image = self.make_article(slug="newest", image="", days=0)
        featured = self.make_article(slug="with-image", image="blog/editorial.jpg", days=1)

        with self.assertNumQueries(2):
            articles = list(get_latest_articles(6))
        self.assertEqual(articles[0], newest_without_image)

        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        self.assertEqual(english.context["editorial_article"], featured)
        self.assertContains(english, featured.title)
        self.assertContains(english, featured.summary)
        self.assertContains(english, "/en/articles/with-image/")
        self.assertContains(persian, featured.title_fa)
        self.assertContains(persian, featured.summary_fa)

        with self.assertNumQueries(0):
            rendered = render_to_string(
                "products/components/editorial_feature.html",
                {"article": featured},
            )
        self.assertIn(featured.get_absolute_url(), rendered)

    def test_editorial_neutral_fallback_has_canonical_articles_action(self):
        with self.assertNumQueries(0):
            rendered = render_to_string(
                "products/components/editorial_feature.html",
                {"article": None},
            )
        self.assertIn("data-editorial-image-fallback", rendered)
        self.assertIn('/articles/', rendered)

    def test_sale_is_section_mode_only_and_reuses_shared_product_card(self):
        for _ in range(4):
            self.make_product(featured=False)
        response = self.client.get("/en/")
        self.assertContains(response, 'data-home-section="sale"')
        self.assertContains(response, "bg-brand-subtle")
        self.assertContains(response, "$42.00")
        self.assertContains(response, "$50.00")

        home_source = self.source("products/templates/products/home.html")
        self.assertEqual(home_source.count('products/components/product_card.html'), 2)
        self.assertNotIn("sale_product_card", home_source)

    def test_trust_strip_has_only_four_supported_translatable_concepts(self):
        rendered = render_to_string("products/components/trust_strip.html")
        self.assertEqual(rendered.count("data-trust-item"), 4)
        for concept in ("Secure checkout", "Order tracking", "Customer support", "Delivery options"):
            self.assertIn(concept, rendered)
        for unsupported in ("free shipping", "fastest delivery", "lifetime warranty", "expert designers", "guaranteed authenticity"):
            self.assertNotIn(unsupported, rendered.lower())
        self.assertNotRegex(rendered, "[😀-🙏🌀-🫿]")

    def test_editorial_replaces_standalone_articles_and_support_uses_canonical_url(self):
        self.make_article()
        response = self.client.get("/en/")
        self.assertNotContains(response, 'data-discovery-rail="articles"')
        self.assertNotContains(response, 'data-home-section="articles"')
        self.assertContains(response, 'data-home-section="editorial"')
        self.assertContains(response, 'href="/en/support/"')
        self.assertContains(response, "Our support team can help with product questions")

    def test_swiper_lock_and_source_neutrality_contracts(self):
        styles = self.source("theme/static_src/src/styles.css")
        swiper = self.source("static/js/swiper.js")
        home = self.source("products/templates/products/home.html")
        components = home + self.source("products/templates/products/components/editorial_feature.html") + self.source("products/templates/products/components/trust_strip.html")

        self.assertIn("watchOverflow: true", swiper)
        self.assertIn("swiper-button-lock", styles)
        self.assertIn("swiper-pagination-lock", styles)
        self.assertIn(".swiper .swiper-button-prev::after", styles)
        self.assertIn("content: none !important", styles)
        self.assertIn('config.slidesPerView = "auto"', swiper)
        self.assertIn("1280: { slidesPerView: 4", swiper)
        for forbidden in ("ProductType", "length_mm", "width_mm", "thickness_mm", "category.slug ==", "category.slug in"):
            self.assertNotIn(forbidden, components)
